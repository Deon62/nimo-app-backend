from fastapi import APIRouter, Depends

from app.auth import CurrentUser, get_current_user
from app.db import Storage, Supabase, eq, get_db, get_storage

router = APIRouter(prefix="/me", tags=["account"])


@router.delete("")
async def delete_account(
    user: CurrentUser = Depends(get_current_user),
    db: Supabase = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> dict:
    """Deletes the user's recordings, photo and account. Profile, calls and payment methods cascade;
    payment records are kept without the user link (tax records), see migration 0002."""
    for call in await db.select("calls", {"user_id": eq(user.id)}, columns="id"):
        await storage.remove("recordings", await storage.list_paths("recordings", f"{user.id}/{call['id']}"))
    await storage.remove("avatars", await storage.list_paths("avatars", user.id))
    await storage.delete_user(user.id)
    return {"ok": True}
