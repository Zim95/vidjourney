So In Canva, we have 

Elements, Text, Uploads, Draw, Design. We want a similar concept here.

Element:
---------
- A Square with a text in it. Eg: SERVER means a square with a server.

- But If I do have an image with the same name, then its that image.

- These will have their own animation property
  - How they spawn
  - How they stay - Their idle animation
  - How they move (If they move)
    - Moving means they will have to move across a line.
    - It will have starting co-ordinates and ending co-ordinates.
    - Then we need to draw a path between those coordinates
      - Path can be:
        - Straight Line
        - Bent upwards line? looks like an S or a Z
        - Bent downwards line. Again S or Z.
        - Curved upwards Line.
        - Curved downwards line.
        - We will add custom paths later but not now.
  - How they are removed from the canvas.


Here we would have these classes:
1. Element(nameofelement, length, breadth) ----> Server, Cache etc.
2. SpawnAnimation(Element) ----> PopIn
3. IdleAnimation(Element) ----> Idle (Fornow this is it)
4. Movement(Element, StartPosition, EndPosition) ----> NoMovement, StraightLine, UpwardsCurve, DownwardsCurve, UpwardsBent, DownwardsBent.
4. RemoveAnimation(Element) ----> PopOut

Build Structure ----> We will use a builder pattern.
- Element will have a name in the constructor, using which it will use to decide its image, either an image or a square with a text if the image is not available. It will also have a length and a breadth to determine the size.
- Element will have methods: set_spawn_animation, set_idle_animation, set_movement, set_remove_animation.


Edge case: Arrows will need a to and from method, which is an extra method.


Rough LLD:
----------
Lets separate them into different files.

- Element in elements.py
- SpawnAnimation in spawn_animation.py
- IdleAnimation in idle_animation.py
- Movement in movement.py
- RemoveAnimation in remove_animation.py
- ArrowElement in elements.py
  - Inherit Element with extra methods:
    - to_and_from
    - extract_path (we could use this path and plug the path in Movement, so that elements can move across the arrow).


Default Shape Modification:
----------------------------
1. I think the default shape should be a circle.
2. We should have options to put text in the circle called alt_text. Default None
3. We should have options to color put color called alt_color. If None, then its transparent.
4. We should have options to put image called image. Default None.
5. We should have border color called alt_border_color. Default White.

The logic is:
- If image, render image.
- If not, draw a circle with alt_color, alt_border_color and alt_text. We will have atleast alt_border_color so a white circle will spawn.

1. We need a way to define size - we should call the variable size. We need to translate that to a radius and then create a circle out of that.