# Reliable Local Data Tools

This original teaching document concerns the reliability of local software. It offers definitions, implementation principles, failure examples and small engineering methods. It is designed for practice without a network service.

## Reliable publication

### Complete files and the publication boundary

An atomic file replacement means that a reader opening the destination observes either the complete old file or the complete new file. It does not mean that every step used to construct the new file happened at once. The publication boundary is the point where the destination starts referring to the prepared replacement.

Prepare a new document in a temporary file in the destination directory. Finish serialization and validate the complete representation before replacing the destination. Keeping preparation separate from publication allows a preparation error to leave the last usable document intact.

A valid JSON document is not necessarily a valid application result. A record can have correct JSON syntax while omitting its required source identifier. Validate both syntax and the required application fields before publishing the record, so a well-formed but unusable file does not replace a usable one.

Consider a note editor that writes directly into its only saved file. If serialization fails halfway, the previous note has already been damaged. If the editor instead writes a separate candidate and publishes only after validation, the failed serialization leaves the original note available. This example supports staging writes; it does not prove protection against every hardware failure.

### Task identity and safe retries

An operation identifier distinguishes one intended operation from another. Repeating the same completed operation should return its existing outcome rather than create a second record. A different result under the same completed identifier should be rejected because it changes what that identifier means.

A derived result belongs to the exact input state used to produce it. Record a source checksum and a schema version with the operation, then compare them with the current input before application. If the source has changed, reject the old result and create a new operation from the current source; merely changing the old result's checksum does not make it current.

A content checksum alone cannot distinguish two processing generations that happen to produce identical bytes. When generation identity matters, record a generation identifier as well as the checksum. This allows a forced rebuild to invalidate earlier work even when the output text is unchanged.

In a local importer, a validation error is a reason to correct and resubmit the same pending operation. It is not a reason to erase unrelated successful operations. Keeping accepted work as a checkpoint prevents one later failure from forcing every earlier computation to be repeated.

### Two stores and failure experiments

Replacing a file and committing a database transaction are separate events. If a process stops between them, the file can describe new output while the database still records pending work. A design using these two stores must identify this window rather than claim that ordinary file replacement makes the whole operation transactional.

Use a visible pending-apply state when a complete file exists but its task has not committed. Revalidate the operation's input bindings before retrying application. This makes recovery inspectable and avoids marking an unverified file successful merely because it exists.

Test publication failures at the boundary, not just the successful path. For example, save an old document, prepare a new one, inject a failure before the destination switch and assert that readers still obtain the old complete document. Then inject a failure after the switch but before database completion and verify the documented rollback or retry behavior.

Keep failure experiments in temporary directories with their own database. A test should be able to remove its own generated data without touching a real note collection. Isolation makes destructive failure experiments useful without making a user's working collection part of the test fixture.
