import os

base_url = "https://minio.carlboettiger.info"

# make sure h3 is installed.
import duckdb
db = duckdb.connect()
db.install_extension("h3", repository = "community")
db.close()

# enable ibis to use built-in function from the h3 extension
import ibis
from ibis import _
@ibis.udf.scalar.builtin
def h3_cell_to_boundary_wkt	(array) -> str:
    ...


# Configure write-access to source.coop
import streamlit as st
def set_source_secrets(con):

    secret = os.getenv("SOURCE_SECRET")
    if secret is None:
        secret = st.secrets["SOURCE_SECRET"]

    key = os.getenv("SOURCE_KEY")
    if key is None:
        key = st.secrets["SOURCE_KEY"]
    
    query=   f'''
    CREATE OR REPLACE SECRET source (
        TYPE S3,
        KEY_ID '{key}',
        SECRET '{secret}',
        ENDPOINT 'data.source.coop',
        URL_STYLE 'path',
        SCOPE 's3://cboettig'
    );
    
    set THREADS=100;
    '''


    con.raw_sql(query)

def set_aws_secrets(con):    
    query=   f'''
    CREATE OR REPLACE SECRET aws (
        TYPE S3,
        ENDPOINT 's3.us-west-2.amazonaws.com',
        SCOPE 's3://overturemaps-us-west-2/release/'
    );
    '''

    # ENDPOINT 'data.source.coop',

    con.raw_sql(query)

# or write access to minio
def set_secrets(con):
    secret = os.getenv("MINIO_SECRET")
    if secret is None:
        secret = st.secrets["MINIO_SECRET"]

    key = os.getenv("MINIO_KEY")
    if key is None:
        key = st.secrets["MINIO_KEY"]
    
    query=   f'''
    CREATE OR REPLACE SECRET secret2 (
        TYPE S3,
        KEY_ID '{key}',
        SECRET '{secret}',
        ENDPOINT 'minio.carlboettiger.info',
        URL_STYLE 'path',
        SCOPE 's3://public-gbif/'
    );
    '''
    con.raw_sql(query)


import minio
def s3_client(type="minio"):
    minio_key = st.secrets["MINIO_KEY"]
    minio_secret = st.secrets["MINIO_SECRET"]
    client = minio.Minio("minio.carlboettiger.info", minio_key, minio_secret)
    if type == "minio":
        return client

    source_key = st.secrets["SOURCE_KEY"]
    source_secret = st.secrets["SOURCE_SECRET"]
    client = minio.Minio("data.source.coop", source_key, source_secret)
    return client
    

import pydeck as pdk
def HexagonLayer(data, v_scale = 1):
    return pdk.Layer(
            "H3HexagonLayer",
            id="gbif",
            data=data,
            extruded=True,
            get_elevation="value",
            get_hexagon="hex",
            elevation_scale = 50 * v_scale,
            elevation_range = [0,1],
            pickable=True,
            auto_highlight=True,
            get_fill_color="[255 - value, 255, value]",
            )


def terrain_styling():
    maptiler_key = os.getenv("MAPTILER_KEY")
    if maptiler_key is None:
        maptiler_key = st.secrets["MAPTILER_KEY"]    
    terrain_style = {
        "version": 8,
        "sources": {
            "osm": {
                "type": "raster",
                "tiles": ["https://server.arcgisonline.com/ArcGIS/rest/services/NatGeo_World_Map/MapServer/tile/{z}/{y}/{x}.png"],
                "tileSize": 256,
                "attribution": "&copy; National Geographic",
                "maxzoom": 19,
            },
            "terrainSource": {
                "type": "raster-dem",
                "url": f"https://api.maptiler.com/tiles/terrain-rgb-v2/tiles.json?key={maptiler_key}",
                "tileSize": 256,
            },
            "hillshadeSource": {
                "type": "raster-dem",
                "url": f"https://api.maptiler.com/tiles/terrain-rgb-v2/tiles.json?key={maptiler_key}",
                "tileSize": 256,
            },
        },
        "layers": [
            {"id": "osm", "type": "raster", "source": "osm"},
            {
                "id": "hills",
                "type": "hillshade",
                "source": "hillshadeSource",
                "layout": {"visibility": "visible"},
                "paint": {"hillshade-shadow-color": "#473B24"},
            },
        ],
        "terrain": {"source": "terrainSource", "exaggeration": .1},
    }
    return terrain_style
####


## grab polygon of a National park:
import ibis
from ibis import _

import geopandas as gpd
def get_city(name = "Oakland", con = ibis.duckdb.connect()):
    gdf = (con
        .read_geo("/vsicurl/https://data.source.coop/cboettig/us-boundaries/mappinginequality.json")
        .filter(_.city == name)
        .agg(geom = _.geom.unary_union())
       ).execute()
    return gdf 


def get_polygon(name = "New Haven", 
                source = "City",
                _con = ibis.duckdb.connect()):
    match source:
        case 'City':
            gdf = get_city(name, _con)
        case 'Custom':
            gdf = gpd.read_file(name)
        case "All":
            gdf = None
        case _:
            gdf = None
    return gdf

import hashlib
import pandas as pd
def unique_path(gdf_name, rank, taxa, zoom, distinct_taxa):
    #gdf_hash = str(pd.util.hash_pandas_object(gdf).sum())
    text = [gdf_name, rank, taxa, str(zoom), distinct_taxa]
    sig = "-".join(text)
    print(sig)
    sig = hashlib.sha1(sig.encode()).hexdigest()
    dest = "cache/gbif_" + sig + ".json"
    return dest


