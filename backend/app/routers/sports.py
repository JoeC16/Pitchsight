from fastapi import APIRouter, HTTPException

from .. import taxonomy

router = APIRouter()


@router.get("")
def list_sports() -> list[dict]:
    return [{"sport": p["sport"], "name": p["name"]} for p in taxonomy.load_packs().values()]


@router.get("/{sport}")
def get_sport(sport: str) -> dict:
    try:
        return taxonomy.public_pack(sport)
    except taxonomy.TaxonomyError as exc:
        raise HTTPException(404, str(exc)) from None
