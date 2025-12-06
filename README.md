# Deephaven + QuestDB persistent storage
### TLDR;
Deephaven Community doesn't provide built-in connectivity to a DB backend (as of Dec 2025). Here, we leverage QuestDB to add that persistence layer, by   
* subscribing to [Cryptofeed](https://github.com/bmoscon/cryptofeed)'s websockets
* pushing the tick data onto Kafka to create data streams for Deephaven
* persisting all the data to QuestDB to collect historical data
* accessing streams and historical data from the DH UI 


## General Setup 
Everything should "just work", simply run this and wait until all 5 containers start up:<br>
```
docker compose build --no-cache
docker compose up -d  
```
* Deephaven UI is running at http://localhost:10000/ide/
* QuestDB Web UI is running at http://localhost:9000
* Redpanda Console is running at http://localhost:8080/overview
* Data is stored locally under the `/data/[questdb|deephaven]` folders which are mounted into the docker images


