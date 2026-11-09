import VeriSwarm

-- scripts/check_lean.py records this output as evidence that no theorem rests on
-- `sorryAx` or on an added axiom.
#print axioms VeriSwarm.local_respect
#print axioms VeriSwarm.receiver_view_independent
#print axioms VeriSwarm.noninterference
#print axioms VeriSwarm.noninterference_every_prefix
#print axioms VeriSwarm.receiver_completes_job
#print axioms VeriSwarm.backing_state_can_differ
#print axioms VeriSwarm.Temporal.sender_changes_lock
#print axioms VeriSwarm.Temporal.flush_erases_sender
#print axioms VeriSwarm.Temporal.temporal_noninterference
#print axioms VeriSwarm.Temporal.shared_without_flush_leaks
#print axioms VeriSwarm.Mutants.partition_closes_channel
#print axioms VeriSwarm.Mutants.sharedLock_leaks
#print axioms VeriSwarm.Mutants.aliasedBacking_leaks
#print axioms VeriSwarm.Mutants.sharedQuota_leaks
#print axioms VeriSwarm.Mutants.exposedCounter_leaks
#print axioms VeriSwarm.Mutants.workConserving_leaks
#print axioms VeriSwarm.Mutants.weakened_observation_hides_defects
#print axioms VeriSwarm.core_view_independent
#print axioms VeriSwarm.core_noninterference
#print axioms VeriSwarm.runFrom_act_self
#print axioms VeriSwarm.runFrom_act_other
#print axioms VeriSwarm.runFrom_maint
#print axioms VeriSwarm.core_receiver_completes_job
#print axioms VeriSwarm.shared_cell_would_answer_busy
