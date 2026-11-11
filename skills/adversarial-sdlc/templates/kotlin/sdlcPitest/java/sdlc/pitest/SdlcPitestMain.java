package sdlc.pitest;

import java.util.Arrays;
import org.pitest.mutationtest.commandline.MutationCoverageReport;

/**
 * Pitest's command line, plus {@code --mutationUnitSize} from {@code -Dsdlc.pitest.unitSize} (speed spec A3).
 *
 * <p>Pitest runs each analysis unit on one thread, and by default a unit is a whole class, so a class with many
 * mutants runs on one thread however many there are. gradle-pitest-plugin does not expose the option, so the
 * template points the pitest task's main class here. Smaller units change only the schedule: each unit gets a
 * fresh minion, which adds isolation and never removes any.
 *
 * <p>Java on purpose: the Pitest launcher's classpath need not contain the Kotlin standard library.
 */
public final class SdlcPitestMain {
    private SdlcPitestMain() {}

    public static void main(String[] args) {
        String unit = System.getProperty("sdlc.pitest.unitSize", "").trim();
        boolean given = Arrays.stream(args).anyMatch(a -> a.startsWith("--mutationUnitSize"));
        String[] out = args;
        if (!unit.isEmpty() && !given) {
            int n = Integer.parseInt(unit); // a malformed value fails the run, loudly
            if (n < 0) {
                throw new IllegalArgumentException("sdlc.pitest.unitSize must be 0 (one unit per class) or more: " + n);
            }
            out = Arrays.copyOf(args, args.length + 1);
            out[args.length] = "--mutationUnitSize=" + n;
        }
        MutationCoverageReport.main(out);
    }
}
