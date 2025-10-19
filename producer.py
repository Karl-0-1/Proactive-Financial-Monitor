# producer.py: Fetches stock prices and publishes them to the 'stock_feed' topic.

import asyncio
import os
import datetime
from pydantic import BaseModel, Field
import yfinance as yf

from faststream import FastStream, Logger
from faststream.kafka import KafkaBroker

# --- Message Structure ---
# Defines the data format for stock messages using Pydantic for validation.
class StockMessage(BaseModel):
    ticker: str = Field(..., examples=["NVDA"])
    price: float = Field(..., examples=[125.50])
    timestamp: datetime.datetime = Field(default_factory=datetime.datetime.now)

# --- FastStream Setup ---
# Get broker address from environment variable or default to 'redpanda:9092' (service name in Docker Compose)
BROKER_ADDRESS = os.getenv("BROKER_ADDRESS", "redpanda:9092")
# Initialize KafkaBroker: apply_types=True enables automatic Pydantic parsing/validation on the consumer side.
broker = KafkaBroker(BROKER_ADDRESS, apply_types=True)
app = FastStream(broker)
logger = Logger("stock_producer") # Named logger for this service

# Define the publisher that sends messages to the 'stock_feed' topic
stock_publisher = broker.publisher("stock_feed")

# --- Configuration ---
TICKER_SYMBOL = "NVDA" # Stock ticker to monitor
FETCH_INTERVAL_SECONDS = 15 # How often to fetch and publish data (e.g., every 15 seconds)

@app.on_startup
async def run_producer():
    """Main producer loop: runs when the FastStream app starts."""
    logger.info(f"📈 Stock Producer starting for {TICKER_SYMBOL}...")
    ticker = yf.Ticker(TICKER_SYMBOL)

    while True: # Loop indefinitely
        try:
            # Fetch recent 1-minute interval data for a near real-time price
            hist = ticker.history(period="1d", interval="1m")
            current_price = None
            if not hist.empty:
                # Find the last valid (non-NaN) closing price
                 valid_closes = hist['Close'].dropna()
                 if not valid_closes.empty:
                     current_price = valid_closes.iloc[-1]

            # Basic validation before publishing
            if current_price is not None and isinstance(current_price, (int, float)) and current_price > 0:
                # Create a Pydantic message object (includes timestamp)
                message = StockMessage(ticker=TICKER_SYMBOL, price=round(current_price, 2))
                logger.info(f"Publishing Stock: {message.ticker} Price: ${message.price:.2f}")
                # Publish the message object (FastStream handles serialization)
                await stock_publisher.publish(message)
            else:
                 logger.warning(f"No valid recent price found for {TICKER_SYMBOL}.")

        except Exception as e:
            # Log errors concisely to avoid flooding logs during transient issues
            logger.error(f"Error fetching/publishing stock data: {e}", exc_info=False)

        # Wait for the specified interval before the next fetch
        await asyncio.sleep(FETCH_INTERVAL_SECONDS)

# Entry point when the script is run directly (or by Docker)
if __name__ == "__main__":
    # Start the FastStream application, which runs the 'on_startup' function
    asyncio.run(app.run())
