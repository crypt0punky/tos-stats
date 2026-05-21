"""tos-stats ingest package.

Pipeline:
  fetch_cot -> db -> williams + dxy_agg -> narrate -> publish -> post_discord
"""
