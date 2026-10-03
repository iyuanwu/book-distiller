# Reliable Local Data Tools

This original miniature handbook teaches programmers how to build command-line tools that keep local document data consistent. Its subject is software design, file storage, validation and automated testing. The examples are deliberately small so readers can run them without a server.

## Chapter 1: Commands and responsibilities

A command-line interface should turn arguments into a clear request and show the result. Core functions perform the operation. A storage module handles files and database records. Keeping these responsibilities separate lets a test call the core without simulating a terminal.

### Exercise

Write a command that accepts a file path and prints its size. Move the size calculation into a function. Test the function with an empty file and a file containing three bytes. The command should report an actionable error when a file is missing.

## Chapter 2: Publishing complete files

A reader should see either the old complete document or the new complete document. Write new JSON into a temporary file in the destination directory, validate it, and replace the destination only after the write succeeds. Keep the old result intact if preparation fails.

File replacement and a database transaction are separate operations. Document the interruption window and record enough task state to make retry safe. Do not promise that two independent storage systems commit as one transaction.

## Chapter 3: Bounded document processing

For a large line-delimited JSON document, iterate over records rather than loading the entire body into a list. A task that needs twenty examples can retain a bounded set of excerpts while scanning. Record which items were selected and which were omitted so the result can be audited.

A checksum identifies the bytes used for a task. Pair it with a schema version and an operation identifier. Before applying a derived result, verify that these bindings still match the source state. Reject stale output rather than silently attaching it to a changed document.

## Chapter 4: Testing failure paths

Successful examples are only one part of testing. Also test malformed input, duplicate commands, permission failures and interruptions between publication and database completion. Use temporary directories so the tests cannot alter a real document collection.

### Practice project

Build a local note importer with an explicit input model, a command-line interface, an atomic output writer and isolated tests. Add no network service. Demonstrate a failed write that preserves the previous note and a repeated operation that does not create a duplicate record.
