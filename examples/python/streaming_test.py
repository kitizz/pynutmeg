import pynutmeg
import time
import numpy as np

fig = pynutmeg.figure("test", '../figures/figure_stream.qml')
fig.set_gui('../gui/gui1.qml')

sld = fig.parameter('sigma')
sld.set(0.5)

# Check for changes in Gui values
while True:
    time.sleep(0.005)
    # pynutmeg.check_errors()
    sigma = sld.read()
    y = sigma * np.random.standard_normal()

    fig.invoke('ax.blue.appendY', y)
    fig.set('ax', minY=-1, maxY=1)
