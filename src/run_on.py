import datetime
from pipeline import run_region

ON = {
    "name": "Ontario",
    "dir": "data/on",
    "metric_crs": "EPSG:3161",   # Ontario MNR Lambert
    "attribution": ("Contains information licensed under the Open Government Licence – Ontario. "
                    "Sources: MLAS active mining claim cells (OGSEarth, daily), Ontario Mineral "
                    "Deposit Inventory (MDI), OGS Drill Hole Database. Verify in MLAS before staking."),
    "today": datetime.date.today().isoformat(),
    "inline_claims": True,   # no public claims WMS — inline claim cells near leads
}

if __name__ == "__main__":
    run_region(ON)
