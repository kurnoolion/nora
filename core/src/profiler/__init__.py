# Stage code version for the stage graph (strand stage-graph, D-DRAFT-2).
# Bump when the logic of this package changes what it produces from the same
# inputs; the fingerprints of every build then change for this stage and its
# downstream stages. `core/tests/test_pipeline_stage_graph.py` pins each
# version to a hash of the package source as the reminder.
CODE_VERSION = "1"
