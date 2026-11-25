Source reference
================

Ontology schema
---------------

The :download:`release schema <../schema/wsys.yaml>` lists the LinkML modules
included in a release. Use the :doc:`module reference <modules>` to find class
definitions and relationships, and :doc:`rules` for the validation constraints.

.. toctree::
   :maxdepth: 1

   schema-sources

Python API
----------

:py:mod:`loaders.swmm_to_rows` reads a SWMM model into ontology rows.
:py:mod:`generators.linkml_to_sqlite` builds a SQLite artifact from those rows;
:py:mod:`deploy.sqlite_releases` manages releases. The module index also covers
the generators, validation harness and migration utilities.

.. toctree::
   :maxdepth: 2

   Generators <autoapi/generators/index>
   Loaders <autoapi/loaders/index>
   Validation harness <autoapi/harness/index>

.. toctree::
   :maxdepth: 1
   :caption: Deployment and migrations
   :glob:

   autoapi/deploy/*/index
   autoapi/migrations/*/index
