import Nutmeg.Gui 0.1
import QtQuick 2.4

Gui {
    Column {
        width: parent.width
        Slider {
            handle: "sigma"
            decimals: 1
            slider {
                minimumValue: 0
                maximumValue: 4
                stepSize: 0.1
            }

        }

        Button {
            handle: "button"
            text: "Test Button"
        }
    }
}
