import sys
import math
import numpy as np
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QSurfaceFormat
from OpenGL.GL import *
from OpenGL.GL.shaders import compileProgram, compileShader
import OpenGL.GL as gl

# --- Shaders ---
VERTEX_SHADER = """
#version 330 core
layout (location = 0) in vec3 aPos;

uniform mat4 model;
uniform mat4 view;
uniform mat4 projection;

void main()
{
    gl_Position = projection * view * model * vec4(aPos, 1.0);
}
"""

FRAGMENT_SHADER = """
#version 330 core
out vec4 FragColor;

uniform vec3 baseColor;
uniform float glowIntensity;

void main()
{
    // A simple approach to a glowing grid: 
    // we use additive blending (GL_SRC_ALPHA, GL_ONE) set in Python,
    // plus we output our target color (e.g., #00d4ff) scaled by a pulsing intensity.
    FragColor = vec4(baseColor * glowIntensity, 0.8);
}
"""

def generate_sphere_wireframe(radius, sectors, stacks):
    vertices = []
    # Generate point coordinates
    for i in range(stacks + 1):
        stack_angle = math.pi / 2 - i * math.pi / stacks
        xy = radius * math.cos(stack_angle)
        z = radius * math.sin(stack_angle)

        for j in range(sectors + 1):
            sector_angle = j * 2 * math.pi / sectors
            x = xy * math.cos(sector_angle)
            y = xy * math.sin(sector_angle)
            vertices.append([x, y, z])

    indices = []
    # Generate indices for wireframe lines
    # We draw horizontal boundaries and vertical boundaries
    for i in range(stacks):
        k1 = i * (sectors + 1)
        k2 = k1 + sectors + 1
        for j in range(sectors):
            # Vertical lines
            if i != 0:
                indices.append(k1)
                indices.append(k2)
            # Horizontal lines
            if i != (stacks - 1):
                indices.append(k1)
                indices.append(k1 + 1)
            
            k1 += 1
            k2 += 1

    return np.array(vertices, dtype=np.float32), np.array(indices, dtype=np.uint32)


class HologramWidget(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.angle = 0.0
        # Timer for ~60fps animation
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(16)

    def initializeGL(self):
        # Enable basic 3D features
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)  # Additive blending for neon glow
        glEnable(GL_LINE_SMOOTH)
        # Using a thicker line gives a better glow look before post-processing bloom
        glLineWidth(2.5)

        # Transparent background for the widget so the desktop shows through it
        glClearColor(0.0, 0.0, 0.0, 0.0)

        # Compile and bind shaders
        self.shader = compileProgram(
            compileShader(VERTEX_SHADER, GL_VERTEX_SHADER),
            compileShader(FRAGMENT_SHADER, GL_FRAGMENT_SHADER)
        )

        # Create sphere geometry
        self.vertices, self.indices = generate_sphere_wireframe(1.2, 32, 24)

        # Send geometry to GPU (VAO, VBO, EBO)
        self.VAO = glGenVertexArrays(1)
        self.VBO = glGenBuffers(1)
        self.EBO = glGenBuffers(1)

        glBindVertexArray(self.VAO)

        glBindBuffer(GL_ARRAY_BUFFER, self.VBO)
        glBufferData(GL_ARRAY_BUFFER, self.vertices.nbytes, self.vertices, GL_STATIC_DRAW)

        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, self.EBO)
        glBufferData(GL_ELEMENT_ARRAY_BUFFER, self.indices.nbytes, self.indices, GL_STATIC_DRAW)

        # Position attribute
        glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 3 * self.vertices.itemsize, None)
        glEnableVertexAttribArray(0)

        glBindBuffer(GL_ARRAY_BUFFER, 0)
        glBindVertexArray(0)

        # Fetch uniform locations for our shader
        self.model_loc = glGetUniformLocation(self.shader, "model")
        self.view_loc = glGetUniformLocation(self.shader, "view")
        self.proj_loc = glGetUniformLocation(self.shader, "projection")
        self.color_loc = glGetUniformLocation(self.shader, "baseColor")
        self.glow_loc = glGetUniformLocation(self.shader, "glowIntensity")

    def resizeGL(self, w, h):
        glViewport(0, 0, w, h)
        aspect = w / h if h > 0 else 1.0
        self.projection_matrix = self._perspective(45.0, aspect, 0.1, 100.0)

    def paintGL(self):
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glUseProgram(self.shader)

        # Pull back the camera
        view_matrix = self._translate(0.0, 0.0, -4.0)

        # Rotate sphere continuously
        self.angle += 0.3
        
        # Introduce a slight axial wobble/tilt
        model_matrix = np.matmul(self._rotate_y(self.angle), self._rotate_x(self.angle * 0.4 + 15.0))

        glUniformMatrix4fv(self.model_loc, 1, GL_TRUE, model_matrix)
        glUniformMatrix4fv(self.view_loc, 1, GL_TRUE, view_matrix)
        glUniformMatrix4fv(self.proj_loc, 1, GL_TRUE, self.projection_matrix)

        # Set color to 'Jarvis Blue' (#00d4ff)
        # RGB fractions roughly (0.0, 0.83, 1.0)
        glUniform3f(self.color_loc, 0.0, 0.83, 1.0)
        
        # Animate the glow intensity (pulsing slightly)
        glow = 1.0 + 0.3 * math.sin(math.radians(self.angle * 3.0))
        glUniform1f(self.glow_loc, glow)

        # Draw the wireframe
        glBindVertexArray(self.VAO)
        glDrawElements(GL_LINES, len(self.indices), GL_UNSIGNED_INT, None)
        glBindVertexArray(0)

    # Quick matrix helpers (math done natively in Python/NumPy to avoid deprecation of legacy GL matrices)
    def _perspective(self, fov, aspect, near, far):
        f = 1.0 / math.tan(math.radians(fov) / 2.0)
        return np.array([
            [f / aspect, 0.0, 0.0, 0.0],
            [0.0, f, 0.0, 0.0],
            [0.0, 0.0, (far + near) / (near - far), (2.0 * far * near) / (near - far)],
            [0.0, 0.0, -1.0, 0.0]
        ], dtype=np.float32)

    def _translate(self, x, y, z):
        return np.array([
            [1.0, 0.0, 0.0, x],
            [0.0, 1.0, 0.0, y],
            [0.0, 0.0, 1.0, z],
            [0.0, 0.0, 0.0, 1.0]
        ], dtype=np.float32)

    def _rotate_y(self, angle_deg):
        rad = math.radians(angle_deg)
        c, s = math.cos(rad), math.sin(rad)
        return np.array([
            [c, 0.0, -s, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [s, 0.0, c, 0.0],
            [0.0, 0.0, 0.0, 1.0]
        ], dtype=np.float32).T # transposed for OpenGL column-major format

    def _rotate_x(self, angle_deg):
        rad = math.radians(angle_deg)
        c, s = math.cos(rad), math.sin(rad)
        return np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, c, s, 0.0],
            [0.0, -s, c, 0.0],
            [0.0, 0.0, 0.0, 1.0]
        ], dtype=np.float32).T


class DraggableHologramWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()

        # Variables to calculate drag offset
        self._dragging = False
        self._drag_position = QPoint()

    def init_ui(self):
        self.setWindowTitle("JARVIS Hologram HUD")
        self.resize(500, 500)
        
        # Transparent Background & Frameless constraints
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Setup surface format explicitly for alpha buffer support early on
        fmt = QSurfaceFormat()
        fmt.setSamples(8)              # 8x MSAA for smoother lines
        fmt.setAlphaBufferSize(8)      # Crucial: allows transparent background across the QOpenGLWidget
        QSurfaceFormat.setDefaultFormat(fmt)

        self.hologram = HologramWidget(self)
        layout.addWidget(self.hologram)

    # Overridden mouse events for dragging functionality
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            event.accept()
