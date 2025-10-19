# news_producer.py: Fetches news headlines and publishes new ones to the 'news_feed' topic.

import asyncio
import os
import datetime
from pydantic import BaseModel, Field
from newsapi import NewsApiClient # Make sure newsapi-python is installed

from faststream import FastStream, Logger
from faststream.kafka import KafkaBroker

# --- Environment Variable Validation ---
# Ensure the NewsAPI key is provided via environment variable
NEWS_API_KEY = os.environ.get("NEWS_API_KEY")
if not NEWS_API_KEY:
    raise ValueError("🚨 NEWS_API_KEY environment variable not set. Cannot start News Producer.")

# --- Message Structure ---
class NewsMessage(BaseModel):
    headline: str = Field(..., examples=["NVIDIA announces new chip"])
    source: str = Field(..., examples=["Reuters"])
    timestamp: datetime.datetime = Field(default_factory=datetime.datetime.now)

# --- NewsAPI Client Initialization ---
try:
    newsapi_client = NewsApiClient(api_key=NEWS_API_KEY)
except Exception as e:
    # Log and handle potential errors during client setup
    print(f"🚨 Error initializing NewsApiClient: {e}")
    newsapi_client = None

# --- FastStream Setup ---
BROKER_ADDRESS = os.getenv("BROKER_ADDRESS", "redpanda:9092")
broker = KafkaBroker(BROKER_ADDRESS, apply_types=True)
app = FastStream(broker)
logger = Logger("news_producer") # Named logger

# Define the publisher for the 'news_feed' topic
news_publisher = broker.publisher("news_feed")

# --- Configuration ---
QUERY = "NVIDIA" # Topic for news search
CHECK_INTERVAL_SECONDS = 60 * 5 # Check for new news every 5 minutes
PAGE_SIZE = 25 # Number of recent articles to fetch per check

# --- State ---
# Use a simple set to track headlines sent *during this specific run* of the container.
# Note: This state is lost if the container restarts. For persistent tracking, use Redis/DB.
seen_headlines_this_run = set()

@app.on_startup
async def run_news_producer():
    """Main news producer loop: runs when the FastStream app starts."""
    logger.info(f"📰 News Producer starting for query '{QUERY}'...")

    if not newsapi_client:
        logger.error("NewsAPI client not initialized. Stopping producer task.")
        return # Exit if the client couldn't be created

    while True: # Loop indefinitely
        try:
            logger.info(f"Fetching latest news...")
            # Fetch recent articles sorted by publication time
            all_articles = newsapi_client.get_everything(
                q=QUERY,
                language="en",
                sort_by="publishedAt",
                page_size=PAGE_SIZE
            )

            articles = all_articles.get('articles', [])
            # Basic validation of the API response structure
            if not isinstance(articles, list):
                logger.warning(f"Unexpected API response format: {type(articles)}. Skipping check.")
                articles = []

            new_found_in_batch = 0
            # Process articles from oldest to newest within the batch to publish chronologically
            for article in reversed(articles):
                headline = article.get('title')
                source = article.get('source', {}).get('name', 'Unknown Source')

                # Validate headline and check if it's already been sent in this run
                if isinstance(headline, str) and headline and headline not in seen_headlines_this_run:
                    message = NewsMessage(headline=headline, source=source)
                    logger.info(f"Publishing News: '{headline}'")
                    await news_publisher.publish(message)
                    seen_headlines_this_run.add(headline) # Track sent headline
                    new_found_in_batch += 1

            if new_found_in_batch == 0:
                 logger.info("No new headlines found in this check.")

        except Exception as e:
            logger.error(f"Error fetching/publishing news data: {e}", exc_info=False)
            # Wait even on error to avoid hammering the NewsAPI if there's a persistent issue
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

        # Wait before the next check cycle
        logger.info(f"Waiting {CHECK_INTERVAL_SECONDS} seconds before next news check...")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)

# Entry point
if __name__ == "__main__":
    asyncio.run(app.run())
