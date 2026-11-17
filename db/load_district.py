import geopandas as gpd
from shapely.geometry import MultiPolygon
from sqlalchemy import create_engine

gdf = gpd.read_file("data/raw/boundaries/original/bnd_sigungu_11_2025_2Q.shp")
gdf = gdf.to_crs(4326)

gdf = gdf[["SIGUNGU_NM", "geometry"]].rename(columns={"SIGUNGU_NM": "name"})
gdf["geometry"] = gdf.geometry.apply(lambda g: MultiPolygon([g]) if g.geom_type == "Polygon" else g)
gdf = gdf.rename_geometry("geom")

assert len(gdf) == 25, len(gdf)
print(gdf["name"].tolist())   # 한글이 제대로 나오는지 확인

engine = create_engine("postgresql+psycopg2://aed:aedpass@localhost:5432/aed")
gdf.to_postgis("district", engine, if_exists="append", index=False)