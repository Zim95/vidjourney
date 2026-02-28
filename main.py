from manim import Scene

from renderer.manim import ElementBuilder, Idle, NoMovement, PopIn, PopOut

class ServerScene(Scene):
    def construct(self):
        server = (
            ElementBuilder(name="SERVER", size=1.8)
            .set_alt_text("SERVER")
            .set_spawn_animation(PopIn(run_time=0.6))
            .set_idle_animation(Idle(duration=0.8))
            .set_movement(NoMovement(duration=0.0))
            .set_remove_animation(PopOut(run_time=0.6))
            .build()
        )

        server.play_full_cycle(self)