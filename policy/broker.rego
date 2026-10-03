package broker
import rego.v1

default allow := false

allow if {
    human_context_valid
    executor_context_valid
    input.agent in {"AgentA", "AgentB"}
    input.allowed_record == {"AgentA": "A", "AgentB": "B"}[input.agent]
    input.request.record == input.allowed_record
    input.request.task == input.allowed_task
    input.allowed_task == "opportunity-summary"
    input.request.action == "read"
    input.request.audience == "salesforce-read"
    count(input.request.fields) > 0
    every field in input.request.fields {
        field in {"Id", "Name", "StageName", "CloseDate", "Amount", "NextStep"}
    }
}

# Baseline mock/human-token tests retain their original contract. Runtime mode
# always sets both required flags in a broker-owned context provider.
human_context_valid if {
    not input.human_session_required
}
human_context_valid if {
    input.human_session_required == true
    input.human.org_id == data.lab.expected_org_id
    input.human.user_id == {"AgentA":data.lab.users.CustomerA.salesforce_user_id,"AgentB":data.lab.users.CustomerB.salesforce_user_id}[input.agent]
    input.human.active == true
    is_string(input.human.session_id)
    count(input.human.session_id) > 0
    input.human.allowed_record == input.allowed_record
    input.human.allowed_task == input.allowed_task
}
executor_context_valid if {
    not input.runtime_executor_required
}
executor_context_valid if {
    input.runtime_executor_required == true
    input.human_session_required == true
    input.executor.org_id == data.lab.expected_org_id
    input.executor.user_id == data.lab.runtime_user_id
    input.executor.validated == true
}

# Readable evidence from the same evaluated rule; no inferred OAuth verification.
deny_reasons contains "human_context_invalid" if { not human_context_valid }
deny_reasons contains "executor_context_invalid" if { not executor_context_valid }
deny_reasons contains "unknown_agent" if { not input.agent in {"AgentA", "AgentB"} }
deny_reasons contains "agent_scope_mismatch" if {
    input.allowed_record != {"AgentA":"A", "AgentB":"B"}[input.agent]
}
deny_reasons contains "target_outside_task_scope" if { input.request.record != input.allowed_record }
deny_reasons contains "task_mismatch" if { input.request.task != input.allowed_task }
deny_reasons contains "task_not_supported" if { input.allowed_task != "opportunity-summary" }
deny_reasons contains "action_not_read" if { input.request.action != "read" }
deny_reasons contains "audience_mismatch" if { input.request.audience != "salesforce-read" }
deny_reasons contains "empty_fields" if { count(input.request.fields) == 0 }
deny_reasons contains "field_not_allowlisted" if {
    some field in input.request.fields
    not field in {"Id", "Name", "StageName", "CloseDate", "Amount", "NextStep"}
}
decision := {"allow": allow, "reasons": sort([reason | reason := deny_reasons[_]])}
