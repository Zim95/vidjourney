Building the basic DSL
-----------------------
Here is an example input
ELEMENT server TYPE square
    TEXT "SERVER"
    POSITION (0,0)
    SPAWN popup 1
    MOVE straight TO (4,2) DURATION 2 REPEAT 2 REVERSE
    REMOVE popout 1
END

This should turn into element json.

The flow is:
-------------
Text DSL -----> Lark Parser -----> AST -----> Transformer -----> SceneModel (Python dataclasses) -----> JSON, just add a to_json to the scenemodel and write that to input/renderer_instructions.json

Steps:
-------
1. We need to create a language definition. A lark file would be needed. Based on the JSON and the code. Can you create a lark file for the language syntax.


Tweaks suggested by GPT:
------------------------
1️⃣ Define Keywords as Explicit Tokens (Critical)

Problem:

IDENT can accidentally match keywords like ELEMENT, MOVE, etc.

This can cause ambiguity in LALR mode.

✅ Fix:

Define all keywords explicitly at the top of grammar:

ELEMENT: "ELEMENT"
TYPE: "TYPE"
TEXT: "TEXT"
POSITION: "POSITION"
SIZE: "SIZE"
IMAGE: "IMAGE"
FILL: "FILL"
BORDER: "BORDER"
SPAWN: "SPAWN"
IDLE: "IDLE"
MOVE: "MOVE"
REMOVE: "REMOVE"
TO: "TO"
PATH: "PATH"
DURATION: "DURATION"
REPEAT: "REPEAT"
REVERSE: "REVERSE"
SEQUENCE: "SEQUENCE"
WAIT: "WAIT"
END: "END"
CLOSE: "CLOSE"

Then update rules to use tokens:

element_block: ELEMENT IDENT TYPE IDENT element_stmt+ END

Do this for all rules.

2️⃣ Restrict Scene Structure (Cleaner v1)

Current:

start: (element_block | sequence_block)*

This allows interleaving declarations and execution.

✅ Recommended:
start: element_block* sequence_block?

This enforces:

All elements declared first

Optional single SEQUENCE block

Cleaner scene lifecycle

You can relax later if needed.

3️⃣ Allow Trailing Comma in PATH (Ergonomic Improvement)

Current:

point_list: "[" point ("," point)* "]"
✅ Improve:
point_list: "[" point ("," point)* ","? "]"

Allows:

PATH [(0,0), (1,1),]

Cleaner UX.

4️⃣ Ensure MOVE in SEQUENCE is Distinguishable

You currently have:

Definition-time:

move_stmt: MOVE IDENT move_path DURATION NUMBER repeat_clause? reverse_clause?

Execution-time:

sequence_stmt: MOVE IDENT

This is valid but your transformer must:

Distinguish between MoveDefinition

And MoveExecution

✅ Add comment for clarity:
// MOVE inside element_block defines movement configuration
// MOVE inside sequence_block triggers execution

No grammar change required — just architectural awareness.

5️⃣ Optional: Enforce At Least 2 Points in PATH

Right now, PATH allows:

PATH [(0,0)]

Which is invalid for movement.

✅ Better:

Keep grammar simple.

Enforce minimum 2 points in transformer validation instead of grammar.

Do NOT complicate grammar.

6️⃣ Optional: Make Color More Strict (Future Improvement)

Current:

color: IDENT | ESCAPED_STRING

This is flexible.

If you want stricter control later:

Define HEX_COLOR token

Or restrict allowed names

For now, keep as is.

7️⃣ Ensure NUMBER Is Consistent

You imported:

%import common.SIGNED_NUMBER -> NUMBER

That’s good.

Keep it.

Do not mix INT and NUMBER inconsistently.

You already use:

INT for repeat

NUMBER for duration

This is correct.

🎯 Final Clean Structure Recommendation

Your cleaned start rule:

start: element_block* sequence_block?

Explicit tokens declared.
Trailing comma allowed.
Transformer handles semantic validation.

🚀 Result

After these tweaks your grammar becomes:

Deterministic

LALR-safe

Production-grade

Extendable

Cleaner for error reporting

If you want next, I can give you:

A minimal models.py dataclass structure

Or a Transformer skeleton that maps parse tree → SceneModel


Lark Input:
-----------
1. Lets generate a dsl_instructions.scene in input.
2. Based on the lark file and the renderer_instructions.json.
3. Generate the equivalent instruction.
4. scene is our format of input.


fixes:
------
1. Type is shape or arrow or image.
2. shape tells you which shape.
3. For image we would have url.


Test Update:
------------
1. Just updated the test case to use image for server instead of shape.
2. Check renderer_instructions.json and update dsl_instructions.scene accordingly.

TextFormat and ImageText Update:
--------------------------------
1. We just added image text and changed the text format.
2. This section does not have to worry about text format.
3. But it has to worry about the text being added to image.
4. Looking at renderer_instructions.json, Add the support to renderer_dsl.lark and dsl_instructions.scene

Turning things into an AST:
---------------------------
Next up, we need to build a dsl parser. Needs to take the scene file, use lark and turn it into an AST.

Fixing Some things:
--------------------
- So we should not have main function in parser and all.
- We need to have that control in main.py.
- Lets wire that up in main.

Add SceneModeller Data Class:
------------------------------
- Next up we need data classes to turn AST to dataclasses.
- These dataclasses need to have a to_json method and write_json method.
- The idea is to finally turn the AST into renderer_instructions.json.
- But first we need to model the scene and then turn that into json and then write to output.
- The output must be configurable and passed from main. In our case, it should be renderer_instructions.json


Cleaning up Main:
-----------------
Why do we still have runner_actions?? dsl-ast dsl-json should not be part of terminal commands.

They should be automated. The idea is input would only have dsl_instructions.scene.

Our job is to generate renderer_instruction.json based on dsl_insturcutions.scene


Moving our configs:
-------------------
Lets move our configs to constants.
Lets create a directory called config with __init__.py and constants.py

Lets move all our DEFAULT configs there and use it directly from there. Not from the terminal.

Lets remove the DEFAULT part from all the constants.

Lets read them from os getenv and keep the current values as default when they are not available in the env.


Fix arrow bug:
--------------
Read the difference between renderer_instructions copy.json and rednerer_instructions.json

The arrow is waaay small. Its not proper. What is the difference in renderer_instruction? Why is DSL causing this issue?


Fix slowness:
-------------
Why is the video so slow?? Did we do something make things slower? For generation and all?

Slowness cause:
---------------
I see whats happening. We are running things sequentially. So idle animation ran for a few seconds then the data circle came up. thats the slowness