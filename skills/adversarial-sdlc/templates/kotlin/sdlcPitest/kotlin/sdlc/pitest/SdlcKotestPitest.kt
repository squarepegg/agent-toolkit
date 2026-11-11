// TestEngineLauncher is @KotestInternal in Kotest 5.9; kotest-extensions-pitest uses the same entry point.
// Pinned to Kotest 5.9.1 by the template; a Kotest upgrade must re-run the AC-1 comparison.
@file:OptIn(io.kotest.common.KotestInternal::class)

package sdlc.pitest

// Pitest test plugin for Kotest with one test unit per root test (adversarial-sdlc, Phase 9 S1.2).
//
// kotest-extensions-pitest makes one unit per spec class, so every mutant runs whole specs and Pitest
// can neither target the covering tests nor stop at the first kill. Here Pitest records coverage per
// root test, runs only the tests that cover a mutant (fastest first) and stops at the first failure.
// A mutant counts as killed exactly when some covering test fails, as before.
//
// Property shrinking is switched off: it only searches for a smaller counterexample after a test has
// already failed, so it never changes whether a mutant is killed.

import io.github.classgraph.ClassGraph
import io.kotest.core.descriptors.Descriptor
import io.kotest.core.filter.TestFilter
import io.kotest.core.filter.TestFilterResult
import io.kotest.core.spec.Spec
import io.kotest.core.test.TestCase
import io.kotest.core.test.TestResult
import io.kotest.engine.TestEngineLauncher
import io.kotest.engine.listener.AbstractTestEngineListener
import io.kotest.property.PropertyTesting
import io.kotest.property.ShrinkingMode
import org.pitest.classinfo.ClassByteArraySource
import org.pitest.help.PitHelpError
import org.pitest.testapi.AbstractTestUnit
import org.pitest.testapi.Configuration
import org.pitest.testapi.Description
import org.pitest.testapi.ResultCollector
import org.pitest.testapi.TestGroupConfig
import org.pitest.testapi.TestPluginFactory
import org.pitest.testapi.TestSuiteFinder
import org.pitest.testapi.TestUnit
import org.pitest.testapi.TestUnitExecutionListener
import org.pitest.testapi.TestUnitFinder
import java.lang.reflect.Modifier
import java.util.Optional
import kotlin.reflect.KClass

class SdlcKotestPluginFactory : TestPluginFactory {
    override fun createTestFrameworkConfiguration(
        config: TestGroupConfig?,
        source: ClassByteArraySource?,
        excludedRunners: MutableCollection<String>?,
        includedTestMethods: MutableCollection<String>?,
    ): Configuration = SdlcKotestConfiguration()

    override fun description(): String = "Kotest, one Pitest test unit per root test (adversarial-sdlc)"

    override fun name(): String = "SdlcKotest"
}

class SdlcKotestConfiguration : Configuration {
    // Pitest sorts test plugins by priority (lowest first) and, per class, uses the first that finds units. At the
    // default (10) this plugin tied with pitest-junit5-plugin and lost on classpath order, so Kotest ran through the
    // JUnit Platform: one engine launch, with a classpath scan, per test. Kotest specs come here first; any other
    // test class (e.g. a JUnit Jupiter fuzz test) finds no unit here and falls through to the next plugin.
    override fun priority(): Int = 1

    override fun testUnitFinder(): TestUnitFinder = SdlcKotestUnitFinder()

    override fun testSuiteFinder(): TestSuiteFinder = TestSuiteFinder { emptyList() }

    override fun verifyEnvironment(): Optional<PitHelpError> = Optional.empty()
}

class SdlcKotestUnitFinder : TestUnitFinder {
    override fun findTestUnits(
        clazz: Class<*>,
        listener: TestUnitExecutionListener?,
    ): MutableList<TestUnit> {
        if (!Spec::class.java.isAssignableFrom(clazz) || Modifier.isAbstract(clazz.modifiers)) return mutableListOf()
        @Suppress("UNCHECKED_CAST")
        val spec = clazz.kotlin as KClass<out Spec>
        val names = rootTestNames(clazz)
        // Unknown structure (no no-arg constructor, no root tests, duplicate names): run the whole spec as
        // one unit, exactly as the spec-level plugin does.
        if (names == null || names.isEmpty() || names.toSet().size != names.size) return mutableListOf(SdlcKotestUnit(spec, null))
        return names.map { SdlcKotestUnit(spec, it) }.toMutableList()
    }

    // FunSpec-style specs register their root tests in the constructor, so listing them runs no test.
    private fun rootTestNames(clazz: Class<*>): List<String>? =
        runCatching {
            val instance = clazz.getDeclaredConstructor().newInstance() as Spec
            instance.rootTests().filter { it.disabled != true }.map { it.name.testName }
        }.getOrNull()
}

class SdlcKotestUnit(
    private val spec: KClass<out Spec>,
    private val rootTest: String?,
) : AbstractTestUnit(Description(if (rootTest == null) spec.java.name else "${spec.java.name} > $rootTest", spec.java)) {
    override fun execute(rc: ResultCollector) {
        KotestScans.disableIfEmpty()
        PropertyTesting.defaultShrinkingMode = ShrinkingMode.Off
        PropertyTesting.shouldPrintShrinkSteps = false
        PropertyTesting.shouldPrintGeneratedValues = false
        val failures = mutableListOf<Throwable>()
        var finished = 0
        val listener =
            object : AbstractTestEngineListener() {
                override suspend fun testFinished(
                    testCase: TestCase,
                    result: TestResult,
                ) {
                    finished++
                    result.errorOrNull?.let { failures += it }
                }

                override suspend fun specFinished(
                    kclass: KClass<*>,
                    result: TestResult,
                ) {
                    result.errorOrNull?.let { failures += it }
                }

                override suspend fun engineFinished(t: List<Throwable>) {
                    failures += t
                }
            }
        var launcher = TestEngineLauncher().withListener(listener).withClasses(spec)
        if (rootTest != null) launcher = launcher.withExtensions(OnlyRootTest(rootTest))
        rc.notifyStart(description)
        launcher.launch()
        when {
            failures.isNotEmpty() -> rc.notifyEnd(description, failures.first())
            // A unit that ran nothing must not pass silently: Pitest's unmutated run would accept it.
            finished == 0 -> rc.notifyEnd(description, AssertionError("sdlc-kotest-pitest: no test ran for ${description.name}"))
            else -> rc.notifyEnd(description)
        }
    }
}

// Kotest scans the classpath for @AutoScan extensions and AbstractProjectConfig subclasses on every engine launch
// (about 0.5 s, measured 2026-11-04), and this plugin launches once per test. The scans are switched off only
// when one scan here finds neither, so they would find nothing: the configuration is then identical. Otherwise
// they stay on (correct, slower).
private object KotestScans {
    private val disabled: Boolean by lazy {
        val found =
            ClassGraph().enableClassInfo().enableAnnotationInfo().scan().use { r ->
                r.getClassesWithAnnotation("io.kotest.core.annotation.AutoScan").isNotEmpty() ||
                    r.getSubclasses("io.kotest.core.config.AbstractProjectConfig").isNotEmpty()
            }
        if (!found) {
            System.setProperty("kotest.framework.classpath.scanning.autoscan.disable", "true")
            System.setProperty("kotest.framework.classpath.scanning.config.disable", "true")
        }
        !found
    }

    fun disableIfEmpty(): Boolean = disabled
}

private class OnlyRootTest(
    private val name: String,
) : TestFilter {
    override fun filter(descriptor: Descriptor): TestFilterResult {
        if (descriptor !is Descriptor.TestDescriptor) return TestFilterResult.Include
        var root: Descriptor.TestDescriptor = descriptor
        while (true) {
            val parent = root.parent
            if (parent is Descriptor.TestDescriptor) root = parent else break
        }
        return if (root.id.value == name) {
            TestFilterResult.Include
        } else {
            TestFilterResult.Exclude("sdlc-kotest-pitest runs one root test per unit")
        }
    }
}
