import pynutmeg
import time
import numpy as np

# nutmeg = pynutmeg.Nutmeg()

fig = pynutmeg.figure("test", '../figures/figure_single.qml')
fig.set_gui('../gui/gui1.qml')

# Grab the GUI controls
sld = fig.parameter('sigma')
btn = fig.parameter('button')
# Adjust the control settings
sld.set(maximumValue=20)

# Define some random data and a function to process it
data = np.random.standard_normal(1000)
def get_y(sigma):
    if sigma == 0:
        return data

    m = int(np.ceil(sigma))
    N = 2*m + 1
    # Generate hamming window
    window = np.interp(np.linspace(-m,m,N), [-sigma, 0, sigma], [0, 1, 0])
    window /= window.sum()
    return np.convolve(data, window, mode='same')

# Set the figure limits and initial data
fig.set('ax', minY=-3, maxY=3)
fig.set('ax.blue', y=data)

sigma = 0
# Check for changes in Gui values
while True:
    time.sleep(0.05)
    fig.nutmeg.check_errors()

    if sld.changed:
        sigma = sld.read()
        print("Sigma changed:", sigma)
    fig.set('ax.blue', y=get_y(sigma))

    if btn.changed:
        print("Button:", btn.read())
