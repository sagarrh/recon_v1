# __init__.py — scout.integrations subpackage marker.
# Purpose: Groups external-service integrations: Bright Data scrape+SERP, feed parsing, sitemap parsing, Slack webhooks.
# Scope: No runtime logic; submodules are imported directly where needed.
# Consumers: scout/nodes/* (web_intelligence, website_diff, blog_monitoring, slack_delivery) and db/sync glue.
