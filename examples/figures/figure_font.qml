Figure {
    id: figure
    Layouts.Column {
        spacing: 0
        Layouts.Repeater {
            count: 3
            Axis {
                handle: "ax"
                
                titleFont.pixelSize: 30
                xAxis.tickFont.italic: true
                yAxis.tickTextColor: "blue"

                LinePlot {
                    handle: "blue"
                    line { color: "#7777FF"; style: "-"; width: 4 }
                }
                LinePlot {
                    handle: "red"
                    line { color: "#FF7777"; style: "-"; width: 4 }
                }
                LinePlot {
                    handle: "green"
                    line { color: "#77FF77"; style: ":"; width: 4 }
                }
            }
        }
    }
}