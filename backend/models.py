from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from . import rules


class ShowBriefIn(BaseModel):
    headliner: str = Field(min_length=1, max_length=120)
    city: str = Field(min_length=2, max_length=120)
    venue_capacity: int = Field(ge=50, le=20000)
    show_date: Optional[str] = None
    budget: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    use_popularity_cap: bool = True

    @field_validator("headliner", "city")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("city")
    @classmethod
    def _city(cls, v: str) -> str:
        return rules.normalize_city(v)  # "NOLA" -> "New Orleans, Louisiana"

    @field_validator("show_date")
    @classmethod
    def _date(cls, v: Optional[str]) -> Optional[str]:
        if v in (None, ""):
            return None
        try:
            d = date.fromisoformat(v)
        except ValueError:
            raise ValueError("show_date must be YYYY-MM-DD")
        if d < date.today():
            raise ValueError("show_date is in the past; leave it empty if the date is not set yet")
        if d.year > 2035:
            raise ValueError("show_date is out of range")
        return d.isoformat()


class ConfirmIn(BaseModel):
    qloo_id: str = Field(min_length=1, max_length=200)


class CityCompareIn(BaseModel):
    city: str = Field(min_length=2, max_length=120)


class NegotiateIn(BaseModel):
    action: Literal["veto", "unveto", "reweight", "widen", "toggle_cap", "set_slot", "clear_slot", "rerun"]
    candidate_id: Optional[str] = None
    weights: Optional[dict] = None
    slot: Optional[Literal["primary", "backup", "alternative"]] = None

    @model_validator(mode="after")
    def _check(self):
        needs_candidate = {"veto", "unveto", "set_slot", "clear_slot"}
        if self.action in needs_candidate and not self.candidate_id:
            raise ValueError(f"'{self.action}' needs candidate_id")
        if self.action == "set_slot" and not self.slot:
            raise ValueError("'set_slot' needs slot")
        if self.action == "reweight":
            if self.weights is None:
                raise ValueError("'reweight' needs weights")
            try:
                self.weights = rules.normalize_weights(self.weights)
            except rules.WeightsError as e:
                raise ValueError(str(e))
        return self
