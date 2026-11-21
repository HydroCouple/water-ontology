"""Sphinx documentation for Water Systems Ontology."""
project = "Water Systems Ontology"
author = "HydroCouple"
extensions = ["myst_parser", "sphinxcontrib.mermaid"]
myst_enable_extensions = ["colon_fence"]
myst_fence_as_directive = ["mermaid"]
myst_heading_anchors = 6
exclude_patterns = ["_build", "index.md"]
html_theme = "pydata_sphinx_theme"
html_logo = "_static/hydrocouple.png"
html_static_path = ["_static"]
html_css_files = ['hydrocouple-palette.css']
html_theme_options = {
    "github_url": "https://github.com/HydroCouple/water-ontology",
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
