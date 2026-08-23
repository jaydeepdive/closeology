import datetime
from pipeline import run_region

BC = {
    "name": "British Columbia",
    "dir": "data/bc",
    "metric_crs": "EPSG:3005",
    "attribution": ("Contains information licensed under the Open Government Licence – "
                    "British Columbia. Source: BC Mineral Titles Online (MTO), MINFILE. "
                    "Drill highlights: MINFILE capsule geology. Verify in MTO before staking."),
    "today": datetime.date.today().isoformat(),
}

if __name__ == "__main__":
    run_region(BC)
