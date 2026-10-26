"""Craveable: a goal- and utility-based restaurant recommendation agent.

Pipeline (each stage is a deterministic planning operator):

    ParseQuery -> RetrieveCandidates -> PropagateConstraints -> SearchSolutions
      -> [RelaxConstraints] -> RankSolutions -> ExplainResults -> PresentResults
"""

__version__ = "1.0.0"
