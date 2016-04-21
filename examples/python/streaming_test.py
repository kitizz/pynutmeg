import pynutmeg
import time
import numpy as np

fig = pynutmeg.figure("test", '../figures/figure_stream.qml')
fig.set_gui('../gui/gui1.qml')

sld = fig.parameter('sigma')
sld.set(0.5)

# fig.set('ax.blue', x=[0,1,2,3,10], y=[-1,2,10,0,3])
time.sleep(5)

# Check for changes in Gui values
while True:
    time.sleep(0.005)
    # pynutmeg.check_errors()
    sigma = sld.read()
    y = sigma * np.random.standard_normal()

    fig.invoke('ax.blue.appendY', y)
