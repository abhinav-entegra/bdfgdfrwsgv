"""Channel/DM permission helpers shared by client HTTP API and realtime (signaling) service."""
from sqlalchemy import or_, and_

from models import db, User, Channel, DMPermission, Workspace, GroupMember


def is_channel_manager(user):
    return user.role in ["admin", "superadmin"] or user.team_role == "teamlead"


def is_public_ecosystem_workspace(ws):
    return ws is not None and ws.id != 1 and not ws.is_private


def get_channel_base_query(user):
    query = Channel.query.filter_by(workspace_id=user.workspace_id)
    if user.workspace_id == 1 and user.team_name:
        explicit_group_ids = [
            row.group_id
            for row in db.session.query(GroupMember.group_id)
            .filter(GroupMember.user_id == user.id)
            .all()
        ]
        if explicit_group_ids:
            query = query.filter(
                or_(Channel.team_name == user.team_name, Channel.id.in_(explicit_group_ids))
            )
        else:
            query = query.filter(Channel.team_name == user.team_name)
    return query


def get_channel_in_context(user, channel_name=None, channel_id=None):
    query = get_channel_base_query(user)
    if channel_name is not None:
        query = query.filter_by(name=channel_name)
    if channel_id is not None:
        query = query.filter_by(id=channel_id)
    return query.first()


def apply_channel_visibility_filter(query, user):
    if is_channel_manager(user):
        return query

    custom_visible_ids = db.session.query(GroupMember.group_id).filter(
        GroupMember.user_id == user.id
    )
    designation = (user.designation or "SE").upper()
    if designation == "SE":
        return query.filter(
            or_(
                Channel.visibility.in_(["all", "se_sse_tl", "se_tl"]),
                and_(Channel.visibility == "custom", Channel.id.in_(custom_visible_ids)),
            )
        )
    if designation == "SSE":
        return query.filter(
            or_(
                Channel.visibility.in_(["all", "se_sse_tl", "sse_tl"]),
                and_(Channel.visibility == "custom", Channel.id.in_(custom_visible_ids)),
            )
        )
    return query.filter(
        or_(
            Channel.visibility == "all",
            and_(Channel.visibility == "custom", Channel.id.in_(custom_visible_ids)),
        )
    )


def get_channel_bulk_roles(channel):
    return {perm.team_role for perm in channel.role_permissions.all()}


def get_channel_explicit_member_ids(channel):
    return {gm.user_id for gm in GroupMember.query.filter_by(group_id=channel.id).all()}


def can_user_view_channel(user, channel):
    if user.is_restricted:
        return False

    if bool(getattr(channel, "is_private_group", False)):
        if user.role in ["admin", "superadmin"]:
            return True
        return user.id in get_channel_explicit_member_ids(channel)
    if (channel.visibility or "").strip().lower() == "custom":
        if user.role in ["admin", "superadmin"]:
            return True
        return user.id in get_channel_explicit_member_ids(channel)
    return (
        apply_channel_visibility_filter(
            Channel.query.filter(Channel.id == channel.id), user
        ).first()
        is not None
    )


def can_user_dm_target(sender, target):
    if not target:
        return False
    if sender.role == "admin" or (sender.team_role or "").strip().lower() == "teamlead":
        return True

    target_is_lead = (target.team_role or "").strip().lower() == "teamlead" or target.role in (
        "admin",
        "superadmin",
    )
    has_grant = (
        DMPermission.query.filter_by(user_id=sender.id, target_id=target.id).first()
        is not None
    )
    allowlist_only = bool(getattr(sender, "dm_allowlist_only", False))
    if target_is_lead or has_grant:
        return True
    if allowlist_only:
        return False
    if sender.workspace_id == 1 and target.workspace_id == 1:
        return False
    if sender.workspace_id == target.workspace_id:
        ws = db.session.get(Workspace, sender.workspace_id)
        if ws and ws.is_private and ws.id != 1:
            return False
    return True
