"""Rebuild reference.ontology_violation from the rule views (dist/assertions/99_refresh_violations.sql). Not yet run against a workspace."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apply import retarget, split_statements  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--dist", default="dist")
ap.add_argument("--catalog", default="water_core")
a = ap.parse_args()

from pyspark.sql import SparkSession  # noqa: E402

spark = SparkSession.builder.getOrCreate()
for stmt in split_statements((Path(a.dist) / "assertions" / "99_refresh_violations.sql").read_text()):
    spark.sql(retarget(stmt, a.catalog))
n = spark.table(f"{a.catalog}.reference.ontology_violation").groupBy("severity").count().collect()
print({r["severity"]: r["count"] for r in n})
