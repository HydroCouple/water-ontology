"""Sphinx documentation for Water Systems Ontology."""
import os
import sys

project = "Water Systems Ontology"
author = "HydroCouple"
extensions = [
    "sphinx.ext.viewcode",
    "autoapi.extension",
    "myst_parser",
]
myst_enable_extensions = ["colon_fence"]
myst_fence_as_directive = ["mermaid"]
myst_heading_anchors = 6
exclude_patterns = ["_build", "index.md"]
html_theme = "pydata_sphinx_theme"
html_logo = "_static/hydrocouple.png"
html_static_path = ["_static"]
html_theme_options = {
    "logo": {"text": project},
    "show_toc_level": 2,
}

# Published MkDocs bookmarks whose punctuation becomes a hyphen under docutils.
# Attach aliases at build time because both affected pages are schema-generated.
def preserve_bookmarks(app, doctree, docname):
    from docutils import nodes

    aliases = {
        "agent-context": {
            "relations-in-topology-edge": "relations-in-topologyedge",
            "relations-in-event-event-link": "relations-in-eventevent_link",
        },
        "diagrams/knowledge-graph": {
            "simulation-lifecycle-hydrocouple-and-hydrocouple-platform":
                "simulation-lifecycle-hydrocouple-and-hydrocoupleplatform",
        },
    }.get(docname, {})
    for section in doctree.findall(nodes.section):
        for heading_id in tuple(section["ids"]):
            if heading_id in aliases:
                section["ids"].append(aliases[heading_id])


def setup(app):
    app.connect("doctree-resolved", preserve_bookmarks)

# Parse the source tree so optional engines and services need not run during a docs build.
autoapi_dirs = ['../generators', '../loaders', '../harness', '../deploy', '../migrations']
autoapi_options = ["members", "undoc-members", "show-module-summary"]
autoapi_add_toctree_entry = False
autoapi_python_use_implicit_namespaces = True
# The ontology migrations are documented release utilities.
autoapi_ignore = ["*/__pycache__/*"]


# -- HydroCouple documentation theme ---------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_hydrocouple"))
import hydrocouple_theme  # noqa: E402

hydrocouple_theme.configure(
    globals(),
    manual_id="water-ontology",
    project_label="Water Systems Ontology",
    repository="https://github.com/HydroCouple/water-ontology",
    hero={
        "kicker": "Water Systems Ontology",
        "title": "A shared vocabulary for water systems",
        "lede": "People edit the LinkML schema; the OWL and SHACL files, Databricks tables, Unity "
                "Catalog metadata and agent context are all generated from it.",
        "actions": [
            {"label": "How it fits together", "url": "architecture", "style": "solid"},
            {"label": "Source reference", "url": "api", "style": "ghost"},
            {"label": "OpenSWMM Engine manual", "url": "https://www.hydrocouple.org/openswmm.engine/", "style": "ghost"},
        ],
    },
)
