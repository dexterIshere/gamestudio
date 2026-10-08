"""Local vision: the poses imposed on generation, and what is read from an image.

`poses` holds the OpenPose templates that ControlNet imposes on a reference;
`detect` estimates the pose of an image and cuts it out, on the machine. This
serves the concepts: drawing them in an imposed pose, measuring the pose they
hold, cutting them out before 3D.
"""
