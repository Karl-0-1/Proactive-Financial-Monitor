# Proactive Financial Monitor Agent (Dockerized) 🤖📈

## Overview 🚀

This project implements an AI-powered Financial Monitoring Agent using LangChain and Google Gemini, running within a Docker environment.

Unlike a simple Q&A bot, this agent **proactively monitors** streams of financial data (stock prices and news headlines) in the background using `faststream`. When predefined conditions are met (e.g., a significant price change), the agent autonomously **generates a concise alert or insight** using the Gemini API, summarizing the situation without waiting for a user prompt.

The entire system runs as a multi-container Docker application, simulating a microservices architecture.

## Features ✨

* **Proactive Monitoring:** Continuously listens to simulated real-time streams of stock prices (`yfinance`) and news headlines (`newsapi-python`).
* **Event Triggering:** Automatically activates when specific conditions are detected (e.g., price change > 5% after a cooldown period).
* **Autonomous Insight Generation:** Uses Google Gemini (`langchain-google-genai`) to analyze the triggering event (combining price data and recent news sentiment stored in memory) and generate a brief, actionable summary.
* **Sentiment Analysis:** Uses Gemini to determine the sentiment (Positive/Negative/Neutral) of incoming news headlines.
* **Dockerized Microservices:** Runs as a multi-container application (broker + producers + agent) managed by Docker Compose for easy setup and continuous operation.

---

## Tech Stack 🛠️

* **Python 3.11**
* **LangChain:** Framework for building the agent logic (prompts, LLM interaction).
* **Google Gemini API:** For sentiment analysis and proactive insight generation.
* **`faststream[kafka]`:** Python library for creating the data stream producers and the monitoring consumer, interacting with the Kafka protocol.
* **`yfinance`:** Library to fetch stock market data for the stock producer.
* **`newsapi-python`:** Library to fetch news articles for the news producer.
* **`pydantic`:** For data validation of messages within `faststream`.
* **Docker & Docker Compose:** For containerizing and orchestrating the multi-service application.
* **Redpanda (via Docker):** Lightweight, Kafka-compatible message broker used for the data streams.

---

## How the Docker Setup Works 🐳

This project runs as a **four-service application** orchestrated by `docker-compose.yml`:

1.  **`redpanda` Service:**
    * Runs the Redpanda message broker in a container.
    * Acts as the central communication hub (like a post office) for the data streams.
    * Listens on port `9092` *inside* the Docker network.

2.  **`stock-producer` Service:**
    * Builds an image using the `Dockerfile`.
    * Runs the `producer.py` script.
    * Continuously fetches stock prices using `yfinance`.
    * Publishes `StockMessage` data to the `stock_feed` topic on the `redpanda` service.

3.  **`news-producer` Service:**
    * Builds an image using the same `Dockerfile`.
    * Runs the `news_producer.py` script.
    * Periodically fetches news headlines using `NewsAPI`.
    * Publishes *new* `NewsMessage` data to the `news_feed` topic on the `redpanda` service.
    * Receives the `NEWS_API_KEY` via an environment variable defined in `docker-compose.yml`.

4.  **`monitor-agent` Service:**
    * Builds an image using the same `Dockerfile`.
    * Runs the `monitor_agent.py` script.
    * Connects to `redpanda` and subscribes to *both* `stock_feed` and `news_feed` topics using `faststream`.
    * Maintains an in-memory state (last price, recent sentiments).
    * Processes incoming stock messages, checks trigger conditions (price change threshold, cooldown).
    * Processes incoming news messages, calls Gemini via LangChain for sentiment analysis.
    * If a stock trigger occurs, calls Gemini via LangChain to generate a proactive insight using the current price, change, and recent sentiment state.
    * Logs all activities and insights.
    * Receives the `GOOGLE_API_KEY` via an environment variable defined in `docker-compose.yml`.

**Key Docker Concepts Used:**

* **`Dockerfile`:** Defines the common environment (Python + libraries) for the producers and agent.
* **`docker-compose.yml`:** Defines the services, builds the images, sets up the network, manages environment variables (like API keys securely), specifies commands, and starts/stops the entire application stack.
* **Networking:** Docker Compose automatically creates a network, allowing services to communicate using their defined service names (e.g., `redpanda:9092`).
* **Environment Variables:** Used to securely pass API keys into the relevant containers without hardcoding them.

---

## Running the Project 🏃

1.  **Prerequisites:**
    * Docker and Docker Compose installed.
    * Google Gemini API Key.
    * NewsAPI Key (free tier available).
2.  **Clone the Repository:** `git clone <https://github.com/Karl-0-1/Proactive-Financial-Monitor>`
3.  **Navigate to Directory:** `cd proactive-monitor`
4.  **Set Environment Variables:** In your terminal, export your API keys:
    ```bash
    export GOOGLE_API_KEY="YOUR_GEMINI_API_KEY_HERE"
    export NEWS_API_KEY="YOUR_NEWS_API_KEY_HERE"
    ```
5.  **Build and Run:**
    ```bash
    # Use sudo if required by your Docker installation
    sudo docker compose up --build
    ```
6.  **Observe:** Watch the logs from all four containers in your terminal. You'll see producers publishing data and the monitor agent receiving messages, analyzing sentiment, and generating insights when trigger conditions are met.
7.  **Stop:** Press `Ctrl+C` in the terminal to stop all containers. Run `sudo docker compose down` to remove them.
