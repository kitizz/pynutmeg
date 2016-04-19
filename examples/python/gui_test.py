import pynutmeg
import time

nutmeg = pynutmeg.Nutmeg()

fig = nutmeg.figure("test", '../figures/figure_single.qml')
fig.set_gui('../gui/gui1.qml')

sld = fig.parameter('sigma')
btn = fig.parameter('button')

fig.set('ax.bluePlot', x=[0,1,2,3,10], y=[-1,2,10,0,3])

fig2 = nutmeg.figure("test2", '../figures/figure_single.qml')

# Check for changes in Gui values
while True:
    time.sleep(0.001)
    nutmeg.check_errors()

    if sld.changed:
        print("Sigma changed:", sld.read())

    if btn.changed:
        print("Button:", btn.read())
