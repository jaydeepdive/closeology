import datetime
from pipeline import run_region

YK = {
    "name": "Yukon",
    "dir": "data/yk",
    "metric_crs": "EPSG:3579",   # Yukon Albers (metres)
    "attribution": ("Contains information from the Yukon Geological Survey and Yukon "
                    "government (GeoYukon) under the Open Government Licence – Yukon. "
                    "Sources: Yukon MINFILE mineral occurrences, Quartz Claims/Leases. "
                    "Verify tenure in the Yukon registry before staking."),
    "today": datetime.date.today().isoformat(),
    "inline_claims": True,
}

if __name__ == "__main__":
    run_region(YK)
