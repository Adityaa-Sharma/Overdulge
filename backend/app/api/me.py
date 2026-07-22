from fastapi import APIRouter, Depends

from app.core.auth import AuthedUser, get_current_user

router = APIRouter()


@router.get("/me")
async def get_me(user: AuthedUser = Depends(get_current_user)) -> dict[str, str]:
    return {"user_id": user.user_id}
