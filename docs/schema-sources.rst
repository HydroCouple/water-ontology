LinkML schema sources
=====================

Start with :download:`wsys.yaml <../schema/wsys.yaml>` to load the release
and its imports. Each module below pairs its class diagram with the YAML
that declares its classes, slots and enumerations.

.. list-table:: Release modules
   :header-rows: 1

   * - Module
     - LinkML source
   * - :doc:`core <diagrams/core>`
     - :download:`core.yaml <../schema/core.yaml>`
   * - :doc:`provenance <diagrams/provenance>`
     - :download:`provenance.yaml <../schema/provenance.yaml>`
   * - :doc:`topology <diagrams/topology>`
     - :download:`topology.yaml <../schema/topology.yaml>`
   * - :doc:`observation <diagrams/observation>`
     - :download:`observation.yaml <../schema/observation.yaml>`
   * - :doc:`event <diagrams/event>`
     - :download:`event.yaml <../schema/event.yaml>`
   * - :doc:`operations <diagrams/operations>`
     - :download:`operations.yaml <../schema/operations.yaml>`
   * - :doc:`coupling <diagrams/coupling>`
     - :download:`coupling.yaml <../schema/coupling.yaml>`
   * - :doc:`analysis_platform <diagrams/analysis-platform>`
     - :download:`analysis_platform.yaml <../schema/analysis_platform.yaml>`
   * - :doc:`analysis <diagrams/analysis>`
     - :download:`analysis.yaml <../schema/analysis.yaml>`
   * - :doc:`model <diagrams/model>`
     - :download:`model.yaml <../schema/model.yaml>`
   * - :doc:`ml <diagrams/ml>`
     - :download:`ml.yaml <../schema/ml.yaml>`

The model module also imports the :download:`SWMM code enumerations
<../schema/swmm_codes.yaml>`. See :doc:`swmm-sqlite` for how the ingestion
profiles and mappings are applied when building a SQLite release.
