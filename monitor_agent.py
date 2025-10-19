# monitor_agent.py: Consumes stock and news messages, maintains state,
# checks triggers, and uses LangChain/Gemini for analysis and insights.

import asyncio
import os
import datetime
from pydantic import BaseModel, Field
from typing import List, Tuple, Dict, Any # For type hinting state

from faststream import FastStream, Logger
from faststream.kafka import KafkaBroker

from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI # Correct class name

# --- Environment Variable Validation ---
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise ValueError("🚨 GOOGLE_API_KEY environment variable not set. Cannot start Agent.")

# --- Message Structures (must match producers) ---
class StockMessage(BaseModel):
    ticker: str
    price: float
    timestamp: datetime.datetime

class NewsMessage(BaseModel):
    headline: str
    source: str
    timestamp: datetime.datetime

# --- LangChain Agent Setup ---
llm = None
news_sentiment_chain = None
proactive_insight_chain = None
try:
    # Initialize the LLM (ensure GOOGLE_API_KEY is valid)
    llm = ChatGoogleGenerativeAI(
        model="gemini-1.5-flash", # Use a reliable and available model
        google_api_key=GOOGLE_API_KEY,
        convert_system_message_to_human=True # Often needed for system prompts
    )
    print("✅ ChatGoogleGenerativeAI model initialized.")

    # Define prompts and chains only if LLM initialization succeeded
    news_sentiment_prompt = ChatPromptTemplate.from_messages([
        ("system", "Analyze the sentiment of the following financial news headline regarding its likely impact on the company's stock price. Respond with only one word: POSITIVE, NEGATIVE, or NEUTRAL."),
        ("human", "Headline: '{headline}'\nSentiment:")
    ])
    news_sentiment_chain = news_sentiment_prompt | llm

    proactive_insight_prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a financial analyst assistant providing concise alerts for NVDA stock. Based *only* on the provided information, generate a brief (1-2 sentence) insight explaining the potential significance. Mention the price change percentage and the overall recent news sentiment."),
        ("human",
         "Stock: {ticker}\n"
         "Current Price: ${current_price:.2f}\n"
         "Previous Price: ${last_price:.2f}\n"
         "Price Change: {price_change_percent:.2f}%\n"
         "Recent News Sentiment (Last {news_count} articles): {recent_news_sentiment}\n\n"
         "**Insight:**")
    ])
    proactive_insight_chain = proactive_insight_prompt | llm
    print("✅ LangChain prompts and chains defined.")

except Exception as e:
    print(f"🚨 Error initializing ChatGoogleGenerativeAI or Chains: {e}")
    print("   Sentiment analysis and proactive insights will be disabled.")
    # Allow the application to run but without LLM features

# --- State Management ---
# In-memory dictionary to store the latest price, recent sentiments, and alert times per ticker.
# Note: State is lost on container restart. Use Redis/DB for persistence.
agent_state: Dict[str, Dict[str, Any]] = {
    "NVDA": { # Initialize state for the target ticker
        "last_price": None,
        "recent_news_sentiments": [], # List of tuples: (timestamp, sentiment)
        "last_alert_time": None
    }
}
MAX_NEWS_HISTORY: int = 3 # Number of recent news sentiments to store/consider
PRICE_CHANGE_THRESHOLD_PERCENT: float = 5.0 # Trigger threshold for price change %
ALERT_COOLDOWN_SECONDS: int = 60 * 10 # Cooldown period between alerts (10 minutes)

# --- FastStream Setup ---
BROKER_ADDRESS = os.getenv("BROKER_ADDRESS", "redpanda:9092")
# Ensure apply_types=True to enable Pydantic parsing based on function type hints
broker = KafkaBroker(BROKER_ADDRESS, apply_types=True)
app = FastStream(broker)
logger = Logger("monitor_agent") # Named logger for this service

# --- Helper Function for Sentiment Analysis ---
async def analyze_news_sentiment(headline: str) -> str:
    """Uses LangChain asynchronously to get the sentiment of a news headline."""
    if not news_sentiment_chain:
        logger.error("Sentiment chain is not available (LLM init likely failed).")
        return "ERROR_LLM_UNAVAILABLE"
    try:
        # Asynchronously invoke the sentiment analysis chain
        response = await news_sentiment_chain.ainvoke({"headline": headline})
        sentiment = response.content.strip().upper()

        # Validate and extract the core sentiment word
        valid_sentiments = ["POSITIVE", "NEGATIVE", "NEUTRAL"]
        extracted_sentiment = "UNKNOWN"
        # Prioritize exact word match within the response
        response_words = sentiment.split()
        for valid in valid_sentiments:
            if valid in response_words:
                extracted_sentiment = valid
                break
        # Fallback: check if the sentiment word is contained anywhere (less precise)
        if extracted_sentiment == "UNKNOWN":
             for valid in valid_sentiments:
                 if valid in sentiment:
                     extracted_sentiment = valid
                     break # Take the first contained match

        if extracted_sentiment == "UNKNOWN":
             logger.warning(f"Unexpected sentiment format: '{response.content}'. Could not extract valid sentiment.")

        return extracted_sentiment
    except Exception as e:
        logger.error(f"Error during sentiment analysis API call for '{headline}': {e}", exc_info=False)
        return "ERROR_API_CALL"

# --- Consumer / Monitor Agent Logic ---

@broker.subscriber("stock_feed", group_id="monitor_group")
async def handle_stock_message(msg: StockMessage):
    """Handler for messages on the 'stock_feed' topic."""
    logger.info(f"Received Stock: {msg.ticker} Price: ${msg.price:.2f}")
    ticker = msg.ticker

    # Basic input validation
    if not isinstance(ticker, str) or not ticker or not isinstance(msg.price, float) or msg.price <= 0:
        logger.warning(f"Skipping invalid stock message: {msg}")
        return

    try:
        # Initialize state if ticker is new
        if ticker not in agent_state:
            agent_state[ticker] = {
                "last_price": msg.price, "recent_news_sentiments": [], "last_alert_time": None
            }
            logger.info(f"Initialized state for {ticker}")
            return

        # Get current state and update price
        last_price = agent_state[ticker]["last_price"]
        current_price = msg.price
        agent_state[ticker]["last_price"] = current_price # Update state

        # Calculate price change if previous price exists
        price_change_percent = 0.0
        if last_price is not None and last_price > 0:
            price_change_percent = ((current_price - last_price) / last_price) * 100
            logger.info(f"{ticker} Price Change: {price_change_percent:.2f}%")
        else:
             logger.info(f"First valid price for {ticker}. No change calculated yet.")
             return # Don't trigger on the first price update after initialization

        # --- Trigger Check ---
        now = datetime.datetime.now()
        last_alert_time = agent_state[ticker]["last_alert_time"]
        time_since_last_alert = (now - last_alert_time).total_seconds() if last_alert_time else float('inf')

        # Check conditions: significant price change AND cooldown period elapsed
        if abs(price_change_percent) >= PRICE_CHANGE_THRESHOLD_PERCENT and time_since_last_alert > ALERT_COOLDOWN_SECONDS:
            logger.warning(f"🚨 TRIGGER: Price change {price_change_percent:.2f}% for {ticker} detected!")
            agent_state[ticker]["last_alert_time"] = now # Reset cooldown timer

            # Prepare context for the insight LLM call
            sentiments_list = [s[1] for s in agent_state[ticker]["recent_news_sentiments"]]
            sentiment_summary = ", ".join(sentiments_list) if sentiments_list else "No recent news"
            news_count = len(sentiments_list)

            # Call the LLM only if the insight chain is available
            if proactive_insight_chain:
                logger.info("Calling LLM for proactive insight...")
                try:
                    # Asynchronously invoke the insight generation chain
                    insight_response = await proactive_insight_chain.ainvoke({
                        "ticker": ticker,
                        "current_price": current_price,
                        "last_price": last_price,
                        "price_change_percent": price_change_percent,
                        "recent_news_sentiment": sentiment_summary,
                        "news_count": news_count
                    })
                    insight = insight_response.content.strip()
                    logger.info(f"💡 Generated Insight:\n{insight}")
                except Exception as llm_e:
                    logger.error(f"Error generating insight: {llm_e}", exc_info=False)
            else:
                logger.error("Insight chain not available. Cannot generate insight.")
        # Optional: Log if trigger conditions were checked but not met
        # else: logger.debug("Trigger conditions not met (price change or cooldown).")

    except Exception as e:
        # Catch-all for unexpected errors within the handler
        logger.error(f"🚨🚨 Unhandled error in handle_stock_message: {e}", exc_info=True)


@broker.subscriber("news_feed", group_id="monitor_group")
async def handle_news_message(msg: NewsMessage):
    """Handler for messages on the 'news_feed' topic."""
    logger.info(f"Received News: '{msg.headline}'")
    ticker = "NVDA" # Target ticker for sentiment state

    # Basic input validation
    if not isinstance(msg.headline, str) or not msg.headline:
        logger.warning(f"Skipping invalid news message: {msg}")
        return

    try:
        # Get sentiment using the helper function
        sentiment = await analyze_news_sentiment(msg.headline)
        logger.info(f"Analyzed Sentiment: {sentiment}")

        # Update the state if sentiment is valid and ticker exists
        if ticker in agent_state and "ERROR" not in sentiment and sentiment != "UNKNOWN":
            sentiments: List[Tuple[datetime.datetime, str]] = agent_state[ticker]["recent_news_sentiments"]
            # Add the new sentiment with its original message timestamp
            sentiments.append((msg.timestamp, sentiment))
            # Sort by timestamp (most recent first) to easily grab the latest
            sentiments.sort(key=lambda item: item[0], reverse=True)
            # Keep only the N most recent entries
            agent_state[ticker]["recent_news_sentiments"] = sentiments[:MAX_NEWS_HISTORY]
            logger.info(f"Updated sentiments for {ticker}: {[s[1] for s in agent_state[ticker]['recent_news_sentiments']]}") # Log only sentiments for brevity
        elif ticker not in agent_state:
             logger.warning(f"State for {ticker} not initialized. Skipping sentiment update.")
        # Error cases logged within analyze_news_sentiment or if state is missing

    except Exception as e:
        # Catch-all for unexpected errors
        logger.error(f"🚨🚨 Unhandled error in handle_news_message: {e}", exc_info=True)

# --- FastStream Lifecycle Hooks ---
@app.on_startup
async def on_startup():
    """Runs when the agent application starts."""
    logger.info("✅ Monitor Agent starting...")
    if not llm:
        logger.warning("LLM initialization failed. Sentiment/Insight features disabled.")

@app.on_shutdown
async def on_shutdown():
    """Runs when the agent application shuts down."""
    logger.info("🛑 Monitor Agent shutting down...")

# Entry point
if __name__ == "__main__":
    asyncio.run(app.run())
