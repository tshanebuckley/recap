#@ File session
// recap: manual counting in Fiji. Started by recap (recap count, the spot check, or the app) as
//   <fiji launcher> --run fiji_count.groovy "session='<session folder>'"
// The session folder is recap's mailbox. recap writes request_<k>.properties (image, title,
// count_infected, position); this script answers with request_<k>.points.csv (saved every second
// while counting) and request_<k>.status (import | skip | cancel). It keeps "alive" up to date so
// recap notices when Fiji is closed, and stops when recap writes a file named "stop".
// The protocol is mirrored by the stand-in Fiji in tests/fake_fiji.py: change both together.

import ij.IJ
import ij.ImagePlus
import ij.gui.GenericDialog
import ij.gui.NonBlockingGenericDialog
import ij.gui.PointRoi
import java.util.concurrent.atomic.AtomicBoolean

def dir = session
def stopFile = new File(dir, "stop")
def running = new AtomicBoolean(true)

Thread.startDaemon {  // heartbeat for recap
    while (running.get()) {
        new File(dir, "alive").text = String.valueOf(System.currentTimeMillis())
        Thread.sleep(1000)
    }
}

def readPoints = { ImagePlus imp ->
    def pts = []
    def roi = imp.getRoi()
    if (roi instanceof PointRoi) {
        def poly = roi.getFloatPolygon()
        def counters = roi.getCounters()
        for (int i = 0; i < poly.npoints; i++) {
            int c = counters != null ? (counters[i] & 0xff) : 0  // low byte = counter number
            pts << [poly.xpoints[i], poly.ypoints[i], c]
        }
    }
    pts
}

def writePoints = { File f, List pts ->
    f.text = "x,y,counter\n" + pts.collect { "${it[0]},${it[1]},${it[2]}" }.join("\n") + "\n"
}

def totals = { List pts, boolean infected ->
    int n = pts.size()
    int pos = pts.count { it[2] == 1 }
    infected ? "Nuclei counted: ${n}   (VRC-: ${n - pos},  VRC+: ${pos})" : "Nuclei counted: ${n}"
}

def countOne = { File req ->
    def p = new Properties()
    req.withInputStream { p.load(it) }
    boolean infected = p.getProperty("count_infected") == "true"
    String base = req.name - ".properties"
    def pointsFile = new File(dir, base + ".points.csv")
    def statusFile = new File(dir, base + ".status")

    def imp = IJ.openImage(p.getProperty("image"))
    if (imp == null) {
        statusFile.text = "skip\ncould not open " + p.getProperty("image")
        return
    }
    imp.setTitle(p.getProperty("title"))
    imp.show()
    IJ.setTool("multipoint")

    def last = []
    def saving = new AtomicBoolean(true)
    Thread.startDaemon {  // save points every second: nothing is lost if Fiji is closed
        while (saving.get()) {
            if (imp.getWindow() != null) {
                def pts = readPoints(imp)
                if (pts != last) { writePoints(pointsFile, pts); last = pts }
            }
            Thread.sleep(1000)
        }
    }

    String how = "Click every nucleus with the Multi-point tool.\n"
    if (infected)
        how += "Counter 0 = VRC- (uninfected), counter 1 = VRC+ (infected).\n" +
               "Switch counters by double-clicking the Multi-point tool icon.\n"
    how += "Zoom with + and -; remove a point with Alt+click."

    String status = null
    while (status == null) {
        def wait = new NonBlockingGenericDialog("recap: " + p.getProperty("position"))
        wait.addMessage(p.getProperty("title") + "\n\n" + how)
        wait.enableYesNoCancel("Done counting", "Skip image")
        wait.setCancelLabel("Stop counting")
        wait.showDialog()
        def pts = imp.getWindow() != null ? readPoints(imp) : last
        if (wait.wasCanceled()) { status = "cancel"; writePoints(pointsFile, pts); break }
        if (!wait.wasOKed()) { status = "skip"; break }
        def ask = new GenericDialog("recap: import counts?")
        ask.addMessage(p.getProperty("title") + "\n\n" + totals(pts, infected) +
                       "\n\nImport these counts into recap?\n(Cancel discards them.)")
        ask.enableYesNoCancel("Import into recap", "Keep counting")
        ask.showDialog()
        if (ask.wasOKed()) { writePoints(pointsFile, pts); status = "import" }
        else if (ask.wasCanceled()) { status = "skip" }
        else if (imp.getWindow() == null) { imp.show() }  // closed meanwhile: reopen to keep counting
    }
    saving.set(false)
    statusFile.text = status
    imp.changes = false
    imp.close()
}

while (!stopFile.exists()) {
    def next = dir.listFiles().findAll {
        it.name.startsWith("request_") && it.name.endsWith(".properties") &&
        !new File(dir, (it.name - ".properties") + ".status").exists()
    }.sort { it.name }
    if (next) countOne(next[0])
    else Thread.sleep(500)
}
running.set(false)
