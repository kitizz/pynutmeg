import pynutmeg
import numpy as np
import time

fig = pynutmeg.figure('imageExample', '../figures/figure_im.qml')

# Let's draw a color image
im = np.zeros((320,640,3), np.uint8)
im[:,:] = (255, 30, 30)  # Soft red
im[155:165, :] = (30, 30, 255)  # Soft blue stripe
fig.set('ax.im.binary', im)

fig.set('ax.data', x=[0, 10, 20, 450, 640], y=[0, 320, 100, 160, 400])

# fig.set('ax.data2', {'x': [0, 1, 2, 800, 1500], 'y': [0, 300, 200, 900, 1000]})
time.sleep(1)
