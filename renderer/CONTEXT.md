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


Why we are using dataclasses:
-----------------------------
Mainly to reduce boilerplate for model-like objects such as Element and ArrowElement.

- They are mostly state containers (name, size, animation strategy objects, colors, image path), so @dataclass auto-generates __init__, __repr__, and comparisons cleanly.
- It keeps the code shorter and easier to evolve when you add/remove fields.
field(default_factory=...) makes default strategy objects safe per instance (avoids shared mutable defaults).
- It also reads like a clear schema, which fits your LLD and builder pattern.
- You could write regular classes instead, but you’d manually maintain a lot of repetitive constructor/setup code.


All Input should come from SceneModeller:
----------------------------------------
- We need a common contract for scenes. Not just JSON.
- We need dataclasses. JSON has variable structure.
- So instead of JSON, we will pass the scenemodeller to the renderer.
- I believe these should be common for all renderes. So why don't we create it in the renderer module outside the manim directory.
- Also we should create classes. SceneModeller ---> ManimSceneModeller and we could just plug in other scenemodellers.
- The SceneModeller should also have a play method which will be implemented by the child classes. So ManimSceneModeller would call the play_full_cycle method.
- So it should be outside the manim directory. Since it will be a common interface that the outside world will use. 
- Lets create an Enum Called SceneModeller Enum which only has Manim in it at the moment. Using that, we could decide the rendering engine.


Restructure SceneModeller:
--------------------------
- We don't want to play the whole thing at once.
- We want to assign the properties and the animation sequence will be decided by the scene modeller.
- For example, we want the server to pop up, then the cache to pop up, we want to draw an arrow from server to cache. Then once everything is done. We clean everything up.
- So every element should have a close method which calls the exit animation. 
- When we call the close method of the scene modeller, the close method of each element should be triggered.
- Lets not play the whole animation with the close at the same time.
- At first, we define the elements.
- Then in scene modeller, we should also have functions for scene planner which will decide when to spawn, when to move and when to remove from canvas.

Do not play the whole cycle before moving to the next element:
--------------------------------------------------------------
- We want to control the sequence of play. Lets make the SceneModeller do it.
- We should spawn elements and maybe not even pop out.
- We can have the close method for each element which will be triggered when we cleanup the scene.


Restructure Code:
------------------
- We are going to split manim into directories.  
- Lets create an elements directory.
  We will now have specific objects: Create objects directory inside elements directory.
  - Object base class:
    - Position - (x, y) coordinates. Which will be the center of the object.

  - Shapes is the base class which inherits Object (shape_objects.py):
    - Shapes base class will have:
      - set_border - Set the border color. Default White
      - set_fill - Set the fill color. Default None
      - set_text - Set the text and text color. Default None
      - set_size - This will be decided by the child class. We can have 3 child classes atm.
      - draw - To be implemented by the child class.

    - Circle Inherits Shape - Size is radius. Draw will draw a circle with the properties.
    - Square Inherits Shape - Size is length. Draw will draw a Square with the properties.
    - Rectange Inherits Shape - Size is breadth. Length is 2 * breadth. Draw will draw a rectangle with the properties.

    NOTE: To show text, we can simply use a shape with text.
  
  - Arrows is the base class which inherits Object(arrow_objects.py):
    - Arrow base class will have:
      - set_border - Set the border color.
      - set_direction - to and from. Coordinates.
      - set_path - We will add a list of coordinates here between to and from. The arrow will be drawn across those points. We need to figure out how to draw a curve using points, a bent arrow using point, a straight arrow is just two points.
      - draw - Will draw an arrow with the above mentioned properties.
    
    - SolidArrow inherits from base Arrow:
      - It will be a solid line with a to and from. (draw method will decide how it looks).
    
    - DottedArrow inherits from base Arrow:
      - It will be a dotted line with a to and from. (draw method will decide how it looks).
  
    - UnidirectionalSolidArrow inherits from SolidArrow:
      - It will have the arrow only at the from side. (draw method will decide how it looks).

    - BidirectionalSolidArrow inherits from SolidArrow:
      - It will have the arrow on both sides. (draw method will decide how it looks).
  
    - UnidirectionalDottedArrow inherits from DottedArrow:
      - It will have the arrow only at the from side. (draw method will decide how it looks).

    - BidirectionalDottedArrow inherits from DottedArrow:
      - It will have the arrow on both sides. (draw method will decide how it looks).

  - Images (image_objects.py):
    - Image base class will have:
      - set_url - Image URL
      - set_size - Size of the image.
      - draw - Render the image.

  
- Next we will have the animations directory.
- Animation (animations.py)
  - duration of transition - Perform the animation and stop
  - object - the object to animate.
  - repeat - Repeat the animation again. If set to true.
  - animate - The method to be implemented by the child class.

- ShapeAnimation Inherits Animation (shape_animations.py)
  - Nothing to do in this class just inherit and pass it on.

  - ShapePopUpAnimation Inherits ShapeAnimation.
    - animate - Pop the element and stop.

  - ShapePopOutAnimation Inherits ShapeAnimation.
    - animate - Pop out the element and stop.

- ArrowAnimation Inherits Animation (arrow_animations.py)
  - DottedArrowUnidirectionalIdleAnimation Inherits ArrowAnimation
    - animate - Those byte byte go style animated dotted lines. to position towards from position.

  - DottedArrowBidirectionalIdleAnimation Inherits ArrowAnimation
    - animate - One half repeats the animation in one way. The other half goes the other way.

  - UnidirectionalDottedArrowSpawnAnimation Inherits ArrowAnimation
    - animate - Should fade - from towards to position. It should look like an arrow being drawm. Duration should be fast.

  - UnidirectionalDottedArrowRemoveAnimation Inherits ArrowAnimation
    - animate - Should fade - from towards to. It should look like an arrow being erased. Duration should be fast.

  - UnidirectionalSolidArrowSpawnAnimation Inherits ArrowAnimation
    - animate - Should fade - from towards to position. It should look like an arrow being drawm. Duration should be fast.

  - UnidirectionalSolidArrowRemoveAnimation Inherits ArrowAnimation
    - animate - Should fade - from towards to. It should look like an arrow being erased. Duration should be fast.

  - BidirectionalSolidArrowSpawnAnimation Inherits ArrowAnimation
    - animate - Split from between towards edges. Fade in.
  
  - BidirectionalDottedArrowSpawnAnimation Inherits ArrowAnimation
    - animate - Split from between towards edges. Fade in.

  - BidirectionalDottedArrowRemoveAnimation Inherits ArrowAnimation
    - animate - Split from between towards edges. Fade out.

  - BidirectionalSolidArrowRemoveAnimation Inherits ArrowAnimation
    - animate - Split from between towards edges. Fade out.

- ImageAnimation Inherits Animation (arrow_animations.py)
  - Nothing to do here, just inherit and pass along.
  - ImagePopUpAnimation Inherits ImageAnimation.
    - animate - Pop the element and stop.

  - ImagePopOutAnimation Inherits ImageAnimation.
    - animate - Pop out the element and stop.


- Finally we will elements.py outside objects and animation directory in the root of elements directory.
- Elements (elements.py):
  - First of the elements directory will have one base class called Elements.
  - Elements will have these methods:
    - set_shape - Set the shape. Will accepet Shape Class. Default Square.
    - set_image - To set the image url.
    - set_spawn_animation - Will set the spawn animation. Infinite duration. It will stay until removed. Will accept Animation Class as input.
    - set_idle_animation - Will set the idle animation. Repeat true. It will stay until removed. Will accept Animation Class as input.
    - set_movement - Will have a source, destination and time in which it needs to reach from source to destination. Will accept Movement Class as input.
    - set_remove_animation - Will set the remove animation. Will accept Animation Class as input.
    - spawn - Will spawn either with Image (preferable) or with Shape, do idle animation, do movement. If repeat is true then it will repeat like a GIF. If repeat is False stop after doing everything once. Repeat will be passed to the animation objects. So that they can handle their respective repeats.
    - close - Will call the remove animation and remove the element from the canvas.

We will decide on movement later. For now, lets build this.


Test Case:
----------
1. Create a server element with Square Shape, Text - Server, Decide the position, PopUp Animation.
2. Create a cache element left of the server - Square Shape, Text - Cache, Decide the position, but make sure its far enough for the arrow to be visible, PopUp Animation.
3. Create a dotted unidirectional arrow from cache to the server. The dotted arrow should have the idle animation. to is Server, from is Cache. UnidirectionalDottedArrowSpawnAnimation.
4. Once done remove all the elements with their specific remove animations.


Scene Modeller and Scene Renderer:
---------------------------------
1. We want to use this as a unified way of talking to all the renderers.
2. I don't see a reason to have scene_modeller and scene_renderer inside manim.
3. Lets move the functionality out into another directory inside renderer called scene_adapter.
4. Inside lets have __init__.py, scene_adapter_base.py where the base class goes,
  manim_scene_adapter.py where manim specific things go.
5. main.py will get an option in the command line argument called renderer. When set to manim it picks manim_scene_adapter.py
6. manim_scene_adapter.py should have a ManimScene subclass which will build stuff based on the input we give it.
7. There should finally be a manim scene runner which will use the command manim -pql <filename>.py <ManimSceneSubclass>

Independent Main:
----------------
1. I dont want main to have any sort of Manim Dependencies. It should be agnostic of that.
2. Everything for manim should be inside manim directory. Lets move them.
3. Main should only have those options. It should have access to the scene modeller thats it.
4. Using that it should do everything it does now.


Ok let me make this clearer.
----------------------------
1. Lets remove scene_adapter entirely.
2. For manim and for everything.
3. Lets create a renderer_adaptor.
  - It should have exactly what manim directory has. Same files, same methods.
  - The only thing is it does not know which APIs to call to draw stuff.
  - If it knows which renderer to use, it can use the same methods from the particular renderer.
  - In this case manim.
4. Does this make sense? The method signature and everything will match exactly that of the manim module. Just that this is renderer agnostic and will chose the corresponding method of the render mentioned.


Cleanup:
--------
1. Ok, you clearly did not understand what I meant.
2. First off remove, manim_scene_adapter.py, scenes.py, remove_renderer_adaptor completely and remove scene_modeller.py
3. manim will only have elements and __init__.py so it does not make sense to have that directory. Move everything inside elements outside to manim directory and fix the imports.
4. Next create a fresh renderer_adaptor directory. Inside there have objects ---> init, arrow_objects, image_objects, object_base, shape_objects.
5. Repeat the same for animations, add whatever you have in manim. All methods everything.
6. Finally add the elements.py in renderer_adaptor
7. However, we should call the respective renderer's method when we methods in this renderer elements. Do you understand? Animation's draw method should call Manim's draw method.
8. This layer should be a one to one mapping to the underlying renderer. That way we will be able to use this layer directly in main. 


Sample Input for our test:
--------------------------
1. Create the sample input for the same SERVER CACHE ARROW test case we mentioned earlier.
2. Write that down in the comments in main.py


Deleted Everything else:
------------------------
1. So I have deleted everything unnecessary. I have only kept the manim directory in renderer.
2. I will read directly from the input renderer instruction and build using that.

ElementBuilder inside elements.py:
----------------------------------
1. The job of this class is to build an element from the elements we get in the json.
2. For example, if shape is given, we call set_shape and so on.
3. I also added a wait command which translates to time.sleep.

Elements refactor:
------------------
1. We should add a type to decide what object we are referring to.
2. For example, type shape would draw shape based on the information.
3. type arrow would draw arrow based on the information.
4. And so on....
5. Lets model Elements and ELements builder to mimic that. 
6. Finally lets create a json file for our test input. Lets sync these 3 together.

Note: manim_runner.py will have the main runner for manim. This is what will read the json, pass it to ElementsBuilder and get the element. It will then play the whole thing according to the sequence.


Code Refactor:
---------------
1. First off instead of if elif, please use dictionaries. Use lambdas where you have to. 
   Maybe create closure functions and use them in lambdas if you have to but do not use if elif else.

2. renderer_instructions.json should be outside of manim directory. Create an input directory at the root of the project. Add the renderer_instruction.json there.

3. Make sure manim_runnder.py reads from there.


Code Refactor: Moving dictionaries to a common file:
-----------------------------------------------------
I believe these dictionaries inside elements.py should be added in manim_constants.py

So that whenever, we add new shapes or arrow types, its all very simple to add in a single file. The rest of the code can function as it should


Even more abstraction:
----------------------
Lets add build methods to each element, objects, animations whatever.
The job of elements builder is to only pass the config it has thats it.

So that way, we can move more of our dictionaries into manim_constants.py


Simplify main:
--------------
In main, lets create a manim runner function which uses subprocess to call manim pql and all that with the options. Lets create a .env file called manim.env with manim configurations and use that directly.

Main should only know which runner to call. Thats it.

Also add manim.env to gitignore


Movement:
---------
- Movement should follow a path. If there's only two points. The movement is a straight line. We could however break them down into multiple points.
- For example, 0,0 -----> 2,2 is a straight movement
- However, we could do, 0,0 --> 1,0 ---> 1,2 ---> 2, 2 This would be a bent movement
- A curve would just be more points across a point.
- We need upwardsbent, downwardsbent, straight and have ways to reverse them.
Repeat can be set as well. This could be used as data flow.
- Reverse should be enabled. The same path should be travelled backwards.
- Repeat option should be set to repeat the same behaviour.

Example JSON
"movement": {
  "path": [[0,0], [1,0], [1,2], [2,2]],
  "duration": 2,
  "repeat": 3,
  "reverse": false
}

Lets create a movement directory inside manim.

- Movement Base class (movements.py)
  - duration
  - repeat
  - reverse
  - object
  - path --> The points of the path.

  StraightMovement Inherits Movement (straight_movement.py)
  - Should follow a straight path across two points.

  BentMovement Inherits Movement (bent_movement.py)
  - break_down_path - Should break down the path if only two points are provided. We should be able to use the path of arrows as well.

  CurveMovement Inherits Movement (curve_movement.py)
  - break_down_path - Should break down the path if only two points are provided. We should be able to use the path of arrows as well. Follow the path if provided basically.

- Make sure to incorporate this in elements and elements builder. Follow the current format of doing this. manim_constants.py should have all the constants. Use dictionary instead of if else.

Test Case:
-----------
In our existing test case.
- Lets create a very small circle. Fill should be white.
- It should go from cache to server.
- Repeat 3 times.

Test Case Update:
------------------
- I have added a server.svg at the root of the directory.
- Use that for server instead of square with text.
- update renderer_instructions.json


ImageText:
----------
1. Lets add image text that appears below the image.
2. That way everyone will know what the image is.
3. Also can we change the text font to Arial instead of times new roman.
4. The text font change should apply to all.