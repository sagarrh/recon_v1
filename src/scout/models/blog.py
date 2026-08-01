# blog.py — Pydantic models for competitor blog/feed detection artifacts.
# Purpose: Defines FeedEntry, BlogPost, and BlogDetectionResult used by the blog_monitoring node.
# Scope: Structured outputs only; no validation logic beyond field patterns/constraints.
# Consumers: scout/nodes/blog_monitoring.py, scout/integrations/feed_parser.py + sitemap_parser.py, scout/state.py.
from datetime import date, datetime

from pydantic import BaseModel, Field


class FeedEntry(BaseModel):
    entry_id: str
    url: str
    title: str
    published: datetime | None = None
    updated: datetime | None = None
    author: str | None = None
    summary: str | None = None
    content_snippet: str | None = None


class BlogPost(BaseModel):
    model_config = {"extra": "ignore"}
    url: str
    title: str
    published_date: date | None = None
    author: str | None = None
    topic: str | None = None
    word_count: int | None = None
    content_type: str = Field(..., pattern="^(blog_post|case_study|press_release|whitepaper|changelog|other)$")
    detection_source: str = Field(..., pattern="^(rss|atom|sitemap|scrape|feed)$")
    competitor_domain: str
    competitor_name: str
    relevance_to_clusters: list[dict]


class BlogDetectionResult(BaseModel):
    model_config = {"extra": "ignore"}
    competitor_name: str
    competitor_domain: str
    feed_url: str | None = None
    feed_type: str | None = None
    sitemap_url: str | None = None
    new_blog_posts: list[BlogPost]
    detection_method: str = Field(..., pattern="^(feed|sitemap|feed\\+sitemap|scrape_fallback|none)$")
    total_entries_scanned: int
    new_entries_found: int
    summary: str
