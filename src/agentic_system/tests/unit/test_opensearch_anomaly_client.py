from agentic_system.integrations import ANOMALY_RESULTS_PATH, OpenSearchAnomalyClient


def test_anomaly_client_preserves_single_result_polling_contract() -> None:
    client = OpenSearchAnomalyClient(
        opensearch_url="http://opensearch:9200/",
        lookback_seconds=300,
        request_timeout_seconds=10,
    )

    assert client.results_url == f"http://opensearch:9200{ANOMALY_RESULTS_PATH}"

    body = client.search_body()
    assert body["size"] == 100
    assert body["query"]["bool"]["filter"][0] == {
        "range": {"anomaly_grade": {"gt": 0}}
    }
    assert "execution_end_time" in body["query"]["bool"]["filter"][1]["range"]
    assert body["sort"] == [{"execution_end_time": {"order": "asc"}}]
    assert "feature_data" not in body["_source"]
