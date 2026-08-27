# Job recovery and process cleanup

## Startup recovery

1. Open the existing `runtime/jobs.sqlite`; initialization is idempotent.
2. Call stale recovery with the current UTC time.
3. Inspect every job moved to `RECOVERY_REQUIRED` and its last `STALE_LEASE_RECOVERED` event.
4. Requeue only jobs whose prior state was `VALIDATING`, `STAGING`, `WAITING_RESOURCE`, `LAUNCHING`,
   or `PRECHECKING`.
5. Do not automatically requeue a job that reached `SOLVING` or a later phase. Preserve its workdir
   and artifacts for inspection.

## Cancellation

A queued job records `CANCEL_REQUESTED` and `CANCELLED` without launching. An active job first records
`CANCEL_REQUESTED`; the supervisor then stops only exact owned processes, registers all completed or
partial artifacts, and records `CANCELLED`.

## Cleanup invariants

- Match both PID and process create time.
- Discover child processes only below a matching owned launcher.
- Attempt application-level graceful stop when an adapter supplies it.
- Terminate leaf-first, wait, then kill only matching survivors.
- Keep `AccessDenied`, already-exited, identity-mismatch, terminated, killed, and remaining outcomes
  distinct in cleanup evidence.
- Never terminate processes by executable name or by scanning the entire Ansys installation tree.
