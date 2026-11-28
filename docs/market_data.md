# Historical market data (E13)

Initial provider contract and offline JSON-stat parser for Spain national quarterly house price index (Eurostat prc_hpi_q, total purchases, 2015=100). See https://ec.europa.eu/eurostat/en/web/user-guides/data-browser/api-data-access/api-detailed-guidelines/api-statistics and https://ec.europa.eu/eurostat/help/copyright-notice.

Live endpoint response could not be retrieved during initial implementation. Do not treat the parser as live-verified or synthetic tests as actual market data. Before publication, verify endpoint filters, data coverage, reuse terms and flags. Download the response separately using the URL in market_data.HPI_URL, then run:

    python -m scripts.import_market_snapshot downloaded.json market_snapshot.json --retrieved-at ACTUAL_ISO_TIMESTAMP

The output is a local versioned snapshot, with source and revision metadata. Missing observations are omitted, not interpolated; #183 rejects gaps rather than computing misleading statistics. No runtime HTTP or scheduled automation (#190). No actual snapshot is included until verified.

Remaining sources: INE regional IPV; Banco de España matched rental yield indicators; Eurostat HICP and rental inflation; redistribution-cleared diversified EUR equity total return. No substitutions or invented values. Historical data is not a forecast.
