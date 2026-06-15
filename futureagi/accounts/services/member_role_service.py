"""Service layer for ``MemberRoleUpdateAPIView``."""

from typing import Any, Optional
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.permissions.utils import can_invite_at_level, get_org_membership


class MemberRoleUpdateError(Exception):
    """Domain error raised by ``update_member_role``.

    ``code`` is the ``error_codes`` key the caller maps to its transport.
    ``status_code`` is the HTTP status to use (400 for input/state errors,
    403 for authz).
    """

    def __init__(self, code: str, status_code: int = 400):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def update_member_role(
    *,
    organization: Organization,
    actor: User,
    target_user_id: UUID,
    org_level: Optional[int] = None,
    ws_level: Optional[int] = None,
    workspace_id: Optional[UUID] = None,
    workspace_access: Optional[list[dict]] = None,
    workspace_access_provided: bool = False,
) -> dict[str, Any]:
    """Apply an org-level and/or workspace-level role update for a target user.

    ``workspace_access_provided`` distinguishes "key omitted" from "key sent
    empty"; the DRF serializer defaults to ``[]`` so the caller has to tell us.

    Returns a ``changes`` dict for audit log + response.
    """
    try:
        target_membership = OrganizationMembership.objects.get(
            user_id=target_user_id,
            organization=organization,
        )
    except OrganizationMembership.DoesNotExist as exc:
        raise MemberRoleUpdateError("MEMBER_NOT_IN_ORG") from exc

    if not target_membership.is_active and not _has_pending_invite(
        organization, target_user_id
    ):
        raise MemberRoleUpdateError("MEMBER_DEACTIVATED_ROLE_UPDATE")

    _validate_workspace_access_in_org(workspace_access, organization)

    changes: dict[str, Any] = {}

    with transaction.atomic():
        if org_level is not None:
            _apply_org_level_change(
                organization=organization,
                actor=actor,
                target_user_id=target_user_id,
                target_membership=target_membership,
                new_level=org_level,
                workspace_access=workspace_access or [],
                workspace_access_provided=workspace_access_provided,
                ws_level=ws_level,
                workspace_id=workspace_id,
                changes=changes,
            )

        if ws_level is not None and workspace_id is not None:
            _apply_ws_level_change(
                actor=actor,
                target_user_id=target_user_id,
                target_membership=target_membership,
                workspace_id=workspace_id,
                ws_level=ws_level,
                changes=changes,
            )

    return changes


def _has_pending_invite(organization: Organization, target_user_id: UUID) -> bool:
    target_user = User.objects.filter(id=target_user_id).first()
    if not target_user:
        return False
    return OrganizationInvite.objects.filter(
        organization=organization,
        target_email__iexact=target_user.email,
        status=InviteStatus.PENDING,
    ).exists()


def _validate_workspace_access_in_org(
    workspace_access: Optional[list[dict]], organization: Organization
) -> None:
    """Reject cross-org workspace_ids; without this the writes below silently
    create a row pointing at a foreign workspace."""
    if not workspace_access:
        return
    ws_ids = [
        entry.get("workspace_id")
        for entry in workspace_access
        if entry.get("workspace_id")
    ]
    if not ws_ids:
        return
    valid_count = Workspace.objects.filter(
        id__in=ws_ids, organization=organization
    ).count()
    if valid_count != len(set(ws_ids)):
        raise MemberRoleUpdateError("WS_NOT_IN_ORG")


def _apply_org_level_change(
    *,
    organization: Organization,
    actor: User,
    target_user_id: UUID,
    target_membership: OrganizationMembership,
    new_level: int,
    workspace_access: list[dict],
    workspace_access_provided: bool,
    ws_level: Optional[int],
    workspace_id: Optional[UUID],
    changes: dict[str, Any],
) -> None:
    old_level = target_membership.level_or_legacy

    actor_membership = get_org_membership(actor)
    actor_level = actor_membership.level_or_legacy if actor_membership else 0
    if not can_invite_at_level(actor_level, new_level):
        raise MemberRoleUpdateError("ROLE_ASSIGN_FORBIDDEN", status_code=403)

    if old_level >= Level.OWNER and new_level < Level.OWNER:
        _enforce_not_last_owner(organization)

    target_membership.level = new_level
    target_membership.role = Level.to_org_string(new_level)
    target_membership.save(update_fields=["level", "role"])
    changes["org_level"] = {"old": old_level, "new": new_level}

    if new_level >= Level.ADMIN:
        _promote_to_workspace_admin_everywhere(
            organization=organization,
            actor=actor,
            target_user_id=target_user_id,
            target_membership=target_membership,
        )
    else:
        _apply_workspace_access(
            organization=organization,
            actor=actor,
            target_user_id=target_user_id,
            target_membership=target_membership,
            new_level=new_level,
            workspace_access=workspace_access,
            workspace_access_provided=workspace_access_provided,
            also_keep_ws_id=workspace_id if ws_level is not None else None,
            changes=changes,
        )

    User.objects.filter(id=target_user_id).update(
        organization_role=Level.to_org_string(new_level)
    )

    target_user = User.objects.filter(id=target_user_id).first()
    if target_user:
        OrganizationInvite.objects.filter(
            organization=organization,
            target_email__iexact=target_user.email,
            status=InviteStatus.PENDING,
        ).update(level=new_level)


def _enforce_not_last_owner(organization: Organization) -> None:
    """``select_for_update`` so a concurrent demote can't push the org below
    one owner."""
    owner_count = (
        OrganizationMembership.objects.select_for_update()
        .filter(organization=organization, is_active=True, level__gte=Level.OWNER)
        .count()
    )
    legacy_owner_count = (
        OrganizationMembership.objects.select_for_update()
        .filter(
            organization=organization,
            is_active=True,
            level__isnull=True,
            role="Owner",
        )
        .count()
    )
    if (owner_count + legacy_owner_count) <= 1:
        raise MemberRoleUpdateError("LAST_OWNER_DEMOTE")


def _promote_to_workspace_admin_everywhere(
    *,
    organization: Organization,
    actor: User,
    target_user_id: UUID,
    target_membership: OrganizationMembership,
) -> None:
    for ws in Workspace.objects.filter(organization=organization):
        WorkspaceMembership._base_manager.update_or_create(
            workspace=ws,
            user_id=target_user_id,
            defaults={
                "level": Level.WORKSPACE_ADMIN,
                "role": Level.to_ws_role(Level.WORKSPACE_ADMIN),
                "organization_membership": target_membership,
                "granted_by": actor,
                "is_active": True,
                "deleted": False,
                "deleted_at": None,
            },
        )


def _apply_workspace_access(
    *,
    organization: Organization,
    actor: User,
    target_user_id: UUID,
    target_membership: OrganizationMembership,
    new_level: int,
    workspace_access: list[dict],
    workspace_access_provided: bool,
    also_keep_ws_id: Optional[UUID],
    changes: dict[str, Any],
) -> None:
    default_ws_level = (
        Level.WORKSPACE_MEMBER if new_level >= Level.MEMBER else Level.WORKSPACE_VIEWER
    )
    for ws_entry in workspace_access:
        ws_id = ws_entry.get("workspace_id")
        ws_level = ws_entry.get("level", default_ws_level)
        if ws_id:
            WorkspaceMembership._base_manager.update_or_create(
                workspace_id=ws_id,
                user_id=target_user_id,
                defaults={
                    "level": ws_level,
                    "role": Level.to_ws_role(ws_level),
                    "organization_membership": target_membership,
                    "granted_by": actor,
                    "is_active": True,
                    "deleted": False,
                    "deleted_at": None,
                },
            )

    if not workspace_access_provided:
        return

    desired_ws_ids: set = {
        entry.get("workspace_id")
        for entry in workspace_access
        if entry.get("workspace_id")
    }
    # Block 2's workspace_id would be re-activated by _apply_ws_level_change
    # below anyway; keep it in the desired set to avoid a revoke + resurrect
    # in the same transaction.
    if also_keep_ws_id is not None:
        desired_ws_ids.add(also_keep_ws_id)

    revoked = (
        WorkspaceMembership._base_manager.filter(
            user_id=target_user_id,
            workspace__organization=organization,
            is_active=True,
        )
        .exclude(workspace_id__in=desired_ws_ids)
        .update(is_active=False, deleted=True, deleted_at=timezone.now())
    )
    if revoked:
        changes["revoked_workspaces"] = revoked


def _apply_ws_level_change(
    *,
    actor: User,
    target_user_id: UUID,
    target_membership: OrganizationMembership,
    workspace_id: UUID,
    ws_level: int,
    changes: dict[str, Any],
) -> None:
    existing_ws = WorkspaceMembership.all_objects.filter(
        workspace_id=workspace_id,
        user_id=target_user_id,
    ).first()
    old_ws = existing_ws.level_or_legacy if existing_ws else None

    WorkspaceMembership.all_objects.update_or_create(
        workspace_id=workspace_id,
        user_id=target_user_id,
        defaults={
            "level": ws_level,
            "role": Level.to_ws_role(ws_level),
            "organization_membership": target_membership,
            "granted_by": actor,
            "is_active": True,
            "deleted": False,
            "deleted_at": None,
        },
    )
    changes["ws_level"] = {"old": old_ws, "new": ws_level}
