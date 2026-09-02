"""Per-jurisdiction ArcGIS configs for the Canada-wide rollout. Each entry drives
arcgis_common.run_fetch() (fetch) and pipeline.run_region() (screen/score). Adding a
province is a config here, not new code. Quebec (WFS) lives in qc_ingest.py.

Endpoints were discovered + verified live against each government's ArcGIS server.
metric_crs = EPSG:3978 (Canada Atlas Lambert, metres) works nationwide for the
buffer/area/nearest ops; occurrence + claim geometry is fetched in EPSG:4326.
"""
import datetime

TODAY = datetime.date.today().isoformat()
LAMBERT = "EPSG:3978"

_NL = ("https://dnrmaps.gov.nl.ca/arcgis/rest/services/GeoAtlas")
_SK = ("https://gis.saskatchewan.ca/arcgis/rest/services/Economy")
_MB = ("https://rdmaps.gov.mb.ca/arcgis/rest/services/MapGallery")
_NT = ("https://www.apps.geomatics.gov.nt.ca/ArcGIS/rest/services/GNWT")
_NB = ("https://gis-erd-der.gnb.ca/server/rest/services/OpenData")
_NSt = ("https://novarocmaps.novascotia.ca/arcgis/rest/services/NovaRoc/MapServer")
_NSo = ("https://dawson.novascotia.ca/arcgis/rest/services/Hosted/"
        "mineral_occurrence_database_d002ns_UT83/FeatureServer/1")
_AB = ("https://gis.energy.gov.ab.ca/arcgis/rest/services/MapServices")


PROVINCES = [
    {   # ---------------- Newfoundland & Labrador ----------------
        "slug": "nl", "name": "Newfoundland & Labrador", "metric_crs": LAMBERT,
        "boundary_name": "Newfoundland and Labrador",
        "attribution": ("Contains data from the Government of Newfoundland & Labrador "
                        "Geoscience Atlas (MODS mineral occurrences, staked claims, mineral "
                        "tenure) under the NL Open Government Licence. Verify tenure before staking."),
        "occ": {"url": f"{_NL}/Map_Layers/MapServer/3",
                "fields": "NMINO,DEPNAME,COMNAME,STATUS,COMMODS,DEPDESC",
                "id": "NMINO", "name": "DEPNAME", "status": "STATUS",
                "comm": "COMMODS", "comm2": "COMNAME", "deptype": "DEPDESC",
                "producer_tokens": ["producer"],
                "url_tmpl": "https://gis.geosurv.gov.nl.ca/mods/ModsCard.asp?NMINOString={id}"},
        "claims": {"url": f"{_NL}/Mineral_Lands/MapServer/0",
                   "fields": "LICENSE_NBR,LOCATION,CLIENT_NAME,STAKEDATE,EXPIRYDATE",
                   "id": "LICENSE_NBR", "name": "LOCATION", "owner": "CLIENT_NAME",
                   "issue": "STAKEDATE", "expiry": "EXPIRYDATE"},
        "reserves": {"url": f"{_NL}/Mineral_Lands/MapServer/5", "name_field": "TYPEDESC"},
    },
    {   # ---------------- Saskatchewan ----------------
        "slug": "sk", "name": "Saskatchewan", "metric_crs": LAMBERT,
        "boundary_name": "Saskatchewan",
        "attribution": ("Contains information from the Government of Saskatchewan "
                        "(SMDI mineral deposits, MARS Crown mineral dispositions, Crown "
                        "reserves) under the Saskatchewan Open Data Licence. Verify tenure before staking."),
        "occ": {"url": f"{_SK}/Mineral_Exploration/MapServer/5",
                "fields": "SMDI,NAME,STATUS,PRIMARYCOMMODITIES,ASSOCIATEDCOMMODITIES,DISCOVERYTYPE,WEBLINK,PRODUCTION",
                "id": "SMDI", "name": "NAME", "status": "STATUS",
                "comm": "PRIMARYCOMMODITIES", "comm2": "ASSOCIATEDCOMMODITIES",
                "deptype": "DISCOVERYTYPE", "url_field": "WEBLINK",
                "producer_field": "PRODUCTION", "producer_tokens": ["producer"],
                "default_status": "Occurrence"},
        "claims": {"url": f"{_SK}/Mineral_Tenure_Crown_Dispositions/MapServer/0",
                   "fields": "DISPOSIT_1,OWNERS,EFFECTIVED,GOODSTANDI,DISPOSIT_3,ISDELETED",
                   "id": "DISPOSIT_1", "owner": "OWNERS", "issue": "EFFECTIVED",
                   "expiry": "GOODSTANDI", "where": "ISDELETED='false'"},
        "reserves": {"url": f"{_SK}/Mining/MapServer/11", "where": "STATUS='ACTIVE'",
                     "name_field": "CR_NUM"},
    },
    {   # ---------------- Manitoba ----------------
        "slug": "mb", "name": "Manitoba", "metric_crs": LAMBERT,
        "boundary_name": "Manitoba", "enrich": "manitoba",
        "attribution": ("Contains information from Manitoba Mineral Resources (mineral "
                        "deposits database, mining claims/leases, mining-restricted areas) "
                        "under the Manitoba Open Government Licence. Verify tenure before staking."),
        "occ": {"url": f"{_MB}/MG_GEOLOGY_CLIENT/MapServer/48",
                "fields": "MINERAL_DEPOSITS_DATABASE_NO,MINERAL_DEPOSITS_DATABASE_NAME,COMMODITY,HOTLINK",
                "id": "MINERAL_DEPOSITS_DATABASE_NO", "name": "MINERAL_DEPOSITS_DATABASE_NAME",
                "comm": "COMMODITY", "url_field": "HOTLINK", "default_status": "Occurrence"},
        "claims": {"url": f"{_MB}/Mineral_Dispositions/MapServer/1",
                   "fields": "CNUMBER,CNAME,HOLDER,RECORDED,STAKED,EXPIRES",
                   "id": "CNUMBER", "name": "CNAME", "owner": "HOLDER",
                   "issue": "RECORDED", "expiry": "EXPIRES"},
        "leases": {"url": f"{_MB}/Mineral_Dispositions/MapServer/2", "id": "LEASE_NUM"},
        "reserves": {"url": f"{_MB}/Mineral_Dispositions/MapServer/21", "name_field": "NAME"},
    },
    {   # ---------------- Northwest Territories ----------------
        "slug": "nt", "name": "Northwest Territories", "metric_crs": LAMBERT,
        "boundary_name": "Northwest Territories",
        "attribution": ("Contains information from the Government of the Northwest "
                        "Territories (NORMIN mineral showings, active mineral claims/leases, "
                        "land withdrawals) and NTGS. Verify tenure before staking."),
        "occ": {"url": ("https://services3.arcgis.com/GSr8HAQhtEt4sNnv/arcgis/rest/services/"
                        "NWTShowings2021a/FeatureServer/0"),
                "fields": "SHOWING_ID,NAME,DEV_STAGE,COMM_ALL,DEPOSIT_TY,RANK",
                "id": "SHOWING_ID", "name": "NAME", "status": "DEV_STAGE",
                "comm": "COMM_ALL", "deptype": "DEPOSIT_TY",
                "producer_tokens": ["producer"], "drill_status_token": "drill"},
        "claims": {"url": f"{_NT}/Economy_LCC/MapServer/1",
                   "fields": "CLAIM_NUM,CLAIM_NAME,OWNERS,ISSUE_DT,ANNIV_DT,CLAIM_STAT",
                   "id": "CLAIM_NUM", "name": "CLAIM_NAME", "owner": "OWNERS",
                   "issue": "ISSUE_DT", "expiry": "ANNIV_DT", "where": "CLAIM_STAT='ACTIVE'"},
        "leases": {"url": f"{_NT}/Economy_LCC/MapServer/2", "id": "CLAIM_NUM"},
        "reserves": {"url": [f"{_NT}/PlanningCadastre_LCC/MapServer/15",
                             f"{_NT}/PlanningCadastre_LCC/MapServer/20"],
                     "name_field": "Name"},
    },
    {   # ---------------- New Brunswick ----------------
        "slug": "nb", "name": "New Brunswick", "metric_crs": LAMBERT,
        "boundary_name": "New Brunswick", "enrich": "new_brunswick",
        "attribution": ("Contains information from the Government of New Brunswick / GeoNB "
                        "(NBGS mineral occurrences, mineral claims) under the GeoNB Open Data "
                        "Licence. Holder/expiry not public — verify tenure before staking."),
        "occ": {"url": f"{_NB}/NBGS_Mineral_Occurrences_Venues_minerales/MapServer/0",
                "fields": "URN,NAME,COMMODITIES,MIN_OCCR_URL",
                "id": "URN", "name": "NAME", "comm": "COMMODITIES",
                "url_field": "MIN_OCCR_URL", "default_status": "Occurrence"},
        "claims": {"url": f"{_NB}/Mineral_Claims/MapServer/0",
                   "fields": "TENURE_NUMBER_ID,TENURE_TYPE_CODE",
                   "id": "TENURE_NUMBER_ID"},
        # no public reserved-from-staking layer for NB
    },
    {   # ---------------- Nova Scotia ----------------
        "slug": "ns", "name": "Nova Scotia", "metric_crs": LAMBERT,
        "boundary_name": "Nova Scotia", "enrich": "nova_scotia",
        "attribution": ("Contains information from the Government of Nova Scotia (NSMOD "
                        "mineral occurrences, NovaRoc exploration licences/leases, restricted "
                        "lands) under the NS Open Data Licence. Verify tenure before staking."),
        "occ": {"url": _NSo,
                "fields": "occ_num,name,status,occ_type,comm_list,comm_prim,hotlink",
                "id": "occ_num", "name": "name", "status": "status",
                "comm": "comm_list", "comm2": "comm_prim", "deptype": "occ_type",
                "url_field": "hotlink", "producer_tokens": ["producer"]},
        "claims": {"url": f"{_NSt}/1",
                   "fields": "TENURE_NUMBER_ID,ISSUE_DATE,GOOD_TO_DATE,EXPIRY_DATE,MINERAL_TENURE_STATUS_CODE",
                   "id": "TENURE_NUMBER_ID", "issue": "ISSUE_DATE", "expiry": "GOOD_TO_DATE",
                   "where": "MINERAL_TENURE_STATUS_CODE='GOOD_STAND'"},
        "leases": {"url": f"{_NSt}/7", "id": "TENURE_NUMBER_ID"},
        "reserves": {"url": [f"{_NSt}/12", f"{_NSt}/23", f"{_NSt}/24", f"{_NSt}/25",
                             f"{_NSt}/27", f"{_NSt}/29", f"{_NSt}/30", f"{_NSt}/32", f"{_NSt}/33"],
                     "name_field": "OBJECTID"},
    },
    {   # ---------------- Alberta (metallic only) ----------------
        "slug": "ab", "name": "Alberta", "metric_crs": LAMBERT,
        "boundary_name": "Alberta",
        "attribution": ("Contains information from the Alberta Geological Survey (metallic "
                        "mineral occurrences) and Alberta Energy (metallic & industrial mineral "
                        "agreements, mineral restrictions). Verify tenure before staking."),
        "occ": {"url": ("https://services2.arcgis.com/jQV6VMr2Loovu7GU/arcgis/rest/services/"
                        "Metallic_Mineral_Occurrences/FeatureServer/0"),
                "fields": "AGS_METID,Name,Dev_Stage,Comm_1,Other_comm,Dep_Type",
                "id": "AGS_METID", "name": "Name", "status": "Dev_Stage",
                "comm": "Comm_1", "comm2": "Other_comm", "deptype": "Dep_Type",
                "producer_tokens": ["producer"]},
        "claims": {"url": f"{_AB}/Agreements/MapServer/4",
                   "fields": "AgreementNumber,DesRep,ReceivedDate,Status",
                   "id": "AgreementNumber", "owner": "DesRep", "issue": "ReceivedDate",
                   "where": "Status='ACTIVE'"},
        "reserves": {"url": f"{_AB}/Mineral_Restrictions/MapServer/0",
                     "where": "MineralType LIKE '%METALLICS%' AND Status='ACTIVE'",
                     "name_field": "RestrictionName"},
    },
]

BY_SLUG = {p["slug"]: p for p in PROVINCES}


def run_region_cfg(p):
    """Adapt a province config into the dict pipeline.run_region expects."""
    return {"name": p["name"], "dir": p["dir"] if "dir" in p else f"data/{p['slug']}",
            "metric_crs": p["metric_crs"], "attribution": p["attribution"],
            "today": TODAY, "inline_claims": True}


# give every province a concrete data dir
for _p in PROVINCES:
    _p["dir"] = f"data/{_p['slug']}"
