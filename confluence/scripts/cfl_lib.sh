# Per-unit bookkeeping for Confluence module jobs (sourced by every rendered module script).
#
# A "unit" is one container call: one reach, metroset or basin for per-reach modules, one
# index range for range-call modules (e.g. consensus), one continent for continent modules.
# Each call is wrapped as `cfl_unit <idx> <command...>`, which appends BEGIN/END rows to a
# per-task TSV under <run>/monitor/units/<module>/ and prints CFL_UNIT_* markers to the log
# so that error lines can be attributed to a unit. The monitor/gate/audit read these.
#
# Required variables (set by the rendered script before sourcing):
#   CFL_MODULE       module name as in modules_to_run (e.g. unconstrained_momma)
#   CFL_MONITOR_DIR  <run>/monitor
#   CFL_INDEX_JSON   json file the unit index points into (may be empty)
#
# Each task writes its own TSV file: appends from many nodes to one file are not atomic on NFS.
# TSV columns: event module array_id task_id idx unit_id rc epoch secs node

CFL_ARRAY_ID="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
CFL_TASK_ID="${SLURM_ARRAY_TASK_ID:-0}"
CFL_NODE="$(hostname -s 2>/dev/null || echo unknown)"
CFL_UNITS_DIR="${CFL_MONITOR_DIR}/units/${CFL_MODULE}"
CFL_UNITS_FILE="${CFL_UNITS_DIR}/${CFL_ARRAY_ID}_${CFL_TASK_ID}.tsv"
CFL_N_UNITS=0
CFL_N_FAILED=0

mkdir -p "${CFL_UNITS_DIR}" 2>/dev/null
# Created up front so that a task which dies before its first unit is still visible.
: >> "${CFL_UNITS_FILE}" 2>/dev/null

# Resolve a unit index to an id: reach_id for reach lists, "<rid>-<rid>-..." for metrosets,
# "basin_<id>" for basins, the continent code for continent.json. Ranges ("12-31") and
# non-numeric indices are returned as-is.
_cfl_unit_id() {
    local idx="$1"
    if [[ "${idx}" =~ ^[0-9]+$ && -n "${CFL_INDEX_JSON}" && -s "${CFL_INDEX_JSON}" ]]; then
        jq -r --argjson i "${idx}" '
            .[$i] |
            if type == "object" then
                if has("basin_id") then "basin_" + (.basin_id | tostring)
                elif has("reach_id") then (.reach_id | tostring)
                else (keys[0] | tostring) end
            elif type == "array" then (map(if type == "object" then (.reach_id | tostring) else tostring end) | join("-"))
            elif . == null then "idx_" + ($i | tostring)
            else tostring end
        ' "${CFL_INDEX_JSON}" 2>/dev/null || echo "idx_${idx}"
    else
        echo "${idx}"
    fi
}

_cfl_row() {
    # event idx unit rc epoch secs
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$1" "${CFL_MODULE}" "${CFL_ARRAY_ID}" "${CFL_TASK_ID}" "$2" "$3" "$4" "$5" "$6" "${CFL_NODE}" \
        >> "${CFL_UNITS_FILE}" 2>/dev/null
}

# cfl_unit <idx> <command...>
# Runs the command, records its exit code, and always returns 0 so a failed unit never stops the loop.
cfl_unit() {
    local idx="$1"
    shift
    local unit t0 rc t1
    unit="$(_cfl_unit_id "${idx}")"
    t0=$(date +%s)
    _cfl_row BEGIN "${idx}" "${unit}" "" "${t0}" ""
    echo "CFL_UNIT_BEGIN module=${CFL_MODULE} idx=${idx} unit=${unit}"
    "$@"
    rc=$?
    t1=$(date +%s)
    _cfl_row END "${idx}" "${unit}" "${rc}" "${t1}" "$((t1 - t0))"
    echo "CFL_UNIT_END module=${CFL_MODULE} idx=${idx} unit=${unit} rc=${rc} secs=$((t1 - t0))"
    CFL_N_UNITS=$((CFL_N_UNITS + 1))
    if [[ "${rc}" -ne 0 ]]; then
        CFL_N_FAILED=$((CFL_N_FAILED + 1))
    fi
    return 0
}

# Task exit code: 0 = every unit exited 0, 3 = some units failed, 4 = every unit failed
# (or none ran). Nothing in the pipeline uses afterok dependencies, so this only makes sacct
# meaningful; per-unit outcomes live in the TSV.
cfl_finish() {
    echo "CFL_TASK_SUMMARY module=${CFL_MODULE} units=${CFL_N_UNITS} failed=${CFL_N_FAILED}"
    if [[ "${CFL_N_UNITS}" -eq 0 || "${CFL_N_FAILED}" -eq "${CFL_N_UNITS}" ]]; then
        exit 4
    elif [[ "${CFL_N_FAILED}" -gt 0 ]]; then
        exit 3
    fi
    exit 0
}
