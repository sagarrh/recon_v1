from pydantic import BaseModel


class CompetitorHistory(BaseModel):
    model_config = {"extra": "ignore"}
    competitor_name: str
    sov_track: list[dict] = []          # sov_tracking rows, oldest->newest
    investigations: list[dict] = []     # investigations rows, oldest->newest


class ClusterHistory(BaseModel):
    model_config = {"extra": "ignore"}
    client_id: str
    cluster_id: str
    cluster_label: str
    competitors: list[CompetitorHistory] = []   # primary + field
    prior_recommendations: list[dict] = []
    outcomes: list[dict] = []
    ai_citation_weeks: list[dict] = []
