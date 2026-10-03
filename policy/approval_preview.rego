package approval_preview
import rego.v1

default allow := false

allow if {
    input.preview_only == true
    input.human_user_id == input.delegated_human_user_id
    input.workload == input.delegated_workload
    input.request.human_user_id == input.human_user_id
    input.request.workload == input.workload
    input.request.task == input.delegated_task
    input.request.action == "update"
    input.request.audience == "salesforce-write-preview"
    input.request.record_id in input.allowed_records
    count(input.request.changes) > 0
    every field in object.keys(input.request.changes) {
        field in input.allowed_fields
        field in {"NextStep", "Amount"}
    }
}
