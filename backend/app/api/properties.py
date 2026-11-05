"""Setup-wizard endpoints: fetch + diff production vs SQA rule trees."""
from fastapi import APIRouter, HTTPException

from app.papi.client import PapiError
from app.schemas import FetchDiffRequest, FetchDiffResponse
from app.services import diff_service

router = APIRouter(prefix="/api/properties", tags=["properties"])


@router.post("/fetch-diff", response_model=FetchDiffResponse)
def fetch_diff(req: FetchDiffRequest) -> FetchDiffResponse:
    try:
        return diff_service.fetch_diff(req)
    except PapiError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
