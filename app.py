import streamlit as st

import ibis
from ibis import _
import pydeck as pdk
from utilities import *
import leafmap.maplibregl as leafmap
import requests
import geopandas as gpd
import altair as alt

st.set_page_config(page_title="Redlining & GBIF", layout="wide")
st.title("Redlining & GBIF")

con = ibis.duckdb.connect(extensions=['httpfs', 'spatial', 'h3'])
set_secrets(con) # s3 credentials
#set_aws_secrets(con)
#set_source_secrets(con)

distinct_taxa = "" # default

col1, col2, col3, col4 = st.columns([1,3,3,3])

# placed outside the form so that toggling this immediately updates the form options available
with col1:
    st.markdown("#### Start 👇")
    area_source = st.radio("Area types", ["City", "All"])  
    nunique = st.toggle("unique taxa only", False)


# config with different default settings by area
config = {
    "City": {
        "names": con.read_parquet("s3://public-gbif/app/city_names.parquet").select("name").execute(),
        "index": 183,
        "zoom": 10,
        "vertical": 0.1,
        "rank_index": 2,
        "taxa": "Aves",
        "unique_rank_index": 6,
    },
     "All": {
        "names": ["All"],
        "index": 0,
        "zoom": 9,
        "vertical": 1.0,
        "rank_index": 2,
        "taxa": "Aves",
        "unique_rank_index": 6,
    }
}

with st.form("my_form"):
    
    taxonomic_ranks = ["kingdom", "phylum", "class", "order", "family","genus", "species"]
    default = config[area_source]

    with col2:
        ## Add additional layer toggles here, e.g. SVI?
        st.markdown("####  🗺️ Select map layers")
        gdf_name = st.selectbox("Area", default["names"], index=default["index"])
        
    with col3:
        st.markdown("#### 🐦 Select taxonomic groups")
        ## add support for multiple taxa!
        rank = st.selectbox("Taxonomic Rank",
                            options=taxonomic_ranks, 
                            index = default["rank_index"])
        taxa = st.text_input("taxa", default["taxa"])
        if nunique:
            distinct_taxa = st.selectbox("Count only unique occurrences by:",
                                         options=taxonomic_ranks,
                                         index = default["unique_rank_index"])

    with col4: 
        st.markdown('''
        #### 🔎 Set spatial resolution
        See [H3 cell size by zoom level](https://h3geo.org/docs/core-library/restable/#cell-areas)
        ''')
        zoom = st.slider("H3 zoom", min_value=1, max_value=11, value = default["zoom"])
        v_scale = st.number_input("vertical scale", min_value = 0.0, value = default["vertical"])

    submitted = st.form_submit_button("Go")

@st.cache_data
def compute_hexes(_gdf, gdf_name, rank, taxa, zoom, distinct_taxa = ""):

    dest = unique_path(gdf_name, rank, taxa, zoom, distinct_taxa)
    bucket = "public-gbif"
    url = base_url + f"/{bucket}/" + dest

    response = requests.head(url)
    if response.status_code != 404:
        return url

    sel = (con
           .read_parquet("s3://public-gbif/app/redlined_cities_gbif.parquet")
           .filter(_[rank] == taxa)
          )

    sel = (sel
           .rename(hex = "h" + str(zoom)) # h3 == 41,150 hexes.  h5 == 2,016,830 hexes
           .group_by(_.hex)
           )

    if distinct_taxa != "": # count n unique taxa
        sel = sel.agg(n = _[distinct_taxa].nunique())
    else: # count occurrences
        sel = sel.agg(n = _.count())

    sel = (sel
           .filter(_.n > 0)
           .mutate(logn = _.n.log())
           .mutate(value = (255 * _.logn / _.logn.max()).cast("int")) # normalized color-scale
    )

    # .to_json() doesn't exist in ibis, use SQL
    query = ibis.to_sql(sel)
    con.raw_sql(f"COPY ({query}) TO 's3://{bucket}/{dest}' (FORMAT JSON, ARRAY true);")

    return url



@st.cache_data
def bar_chart(gdf_name, rank, taxa, zoom, distinct_taxa = ""):
    sel = con.read_parquet("s3://public-gbif/app/redlined_cities_gbif.parquet")
    sel = (sel
           .filter(_[rank] == taxa)
           .mutate(geom = _.geom.convert('EPSG:4326', 'ESRI:54009'))
           .mutate(area = _.geom.area())
          )
    if gdf_name != "All":
        sel = sel.filter(_.city == gdf_name)

    sel = sel.group_by(_.city, _.grade)

    if distinct_taxa: # count n unique taxa
        sel = sel.agg(n = _[distinct_taxa].nunique(), area = _.area.sum()) 
    else:
        sel = sel.agg(n = _.count(), area = _.area.sum())
    sel = (sel
      .mutate(density = _.n /_.area * 10000) # per hectre
      .group_by(_.grade)
      .agg(mean = _.density.mean(),sd = _.density.std())
      .order_by(_.mean.desc())
    )
    
    plt = alt.Chart(sel.execute()).mark_bar().encode(x = "grade", y = "mean")
    return st.altair_chart(plt)

mappinginequality = 'https://data.source.coop/cboettig/us-boundaries/mappinginequality.pmtiles'

redlines = {'version': 8,
 'sources': {'source': {'type': 'vector',
   'url': 'pmtiles://' + mappinginequality,
   'attribution': 'PMTiles'}},
 'layers': [{'id': 'mappinginequality_fill',
   'source': 'source',
   'source-layer': 'mappinginequality',
   'type': 'fill',
   'paint': {'fill-color': ["get", "fill"], 'fill-opacity': 0.9},}
    ]}


count = "occurrences"
if nunique:
    count = "unique " + distinct_taxa



mapcol, chartcol = st.columns([4,1])

if submitted:
    with mapcol:
        gdf = get_polygon(gdf_name, area_source, con)
        url = compute_hexes(gdf, gdf_name, rank, taxa, zoom, distinct_taxa = distinct_taxa)
        layer = HexagonLayer(url, v_scale)
        digest = hashlib.md5(str(layer).encode()).hexdigest()
        print(digest)

        m = leafmap.Map(style=terrain_styling(), center=[-120, 37.6], zoom=2, pitch=35, bearing=10)
        if gdf is not None:
            m.add_gdf(gdf[[gdf.geometry.name]], "fill", paint = {"fill-opacity": 0.2}) # adds area of interest & zooms in
        m.add_pmtiles(mappinginequality, style=redlines, visible=True, opacity = 0.9,  fit_bounds=False)
        m.add_deck_layers([layer])
        m.add_layer_control()
        m.to_streamlit()
        
    with chartcol:
        bar_chart(gdf_name, rank, taxa, zoom, distinct_taxa = distinct_taxa)
        st.markdown("Mean density of " + count + " by redline grade, count per hectre")


    
st.divider()


'''
## Overview

Select an individual city or choose "All" to show all 319 cities in the Mapping Inequality Project.  You can set arbitrary taxonomic filters on what GBIF data is displayed -- e.g. show all of Aves or just show _Canis latrans_. Toggle `unique taxa only` to show either all occurrences or just unique species (or other rank) counts.  The map will show all counts at the selected 'H3 cell' resolution, while the chart on the left shows aggregate counts by redlining grade.  Note that only GBIF data inside graded sectors of the Mapping Inequality maps are shown, occurrences outside these areas have been cropped.  You may need to adjust the vertical scale of map hexes. After making your selections, hit **Go**!  

Map layers may take a while to load on slower networks.  Scroll to zoom, ctrl+click to pivot camera.


## Credits

App developed by Carl Boettiger & Diego Soto, UC Berkeley (2024).

### Data Sources

- Global Biodiversity Information Facility (GBIF) Species Occurrences snapshot on 2024-10-01. Copyright: Public Domain.  Visualization based on pre-computed H3 cell values for all of GBIF, hosted on Source.Coop, <https://source.coop/repositories/cboettig/gbif> as GeoParquet and PMTiles.

- Historical Redlining Data from the Mapping Inequality Project, <https://dsl.richmond.edu/panorama/redlining/>.

### Software

- All open-source software implementation, hosted on HuggingFace Spaces.
- Built with `duckdb`, `maplibre`, `leafmap`, and `streamlit`.
- Source code at <https://github.com/boettiger-lab/redlining-app>

'''

