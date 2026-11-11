plugins {
    id("org.jetbrains.kotlin.jvm") version "1.9.23"
    id("org.jetbrains.kotlin.plugin.allopen") version "1.9.23"
    id("org.jetbrains.kotlin.kapt") version "1.9.23"
}

group = "com.example"
version = "1.0-SNAPSHOT"

repositories {
    mavenCentral()
    maven { url = uri("https://jitpack.io") }
}

allOpen {
    annotation("org.openjdk.jmh.annotations.State") // Required for JMH to work properly with Kotlin classes
}

dependencies {
    implementation("org.jetbrains.kotlin:kotlin-stdlib")

    implementation("org.openjdk.jmh:jmh-core:1.37")
    implementation("org.openjdk.jmh:jmh-generator-annprocess:1.37")

    kapt("org.openjdk.jmh:jmh-generator-annprocess:1.37")

    //implementation("commons-codec:commons-codec:1.18.0") slow blake3
    implementation("org.bouncycastle:bcprov-jdk18on:1.78")

    //implementation("com.github.alephium:blake3-jni:0.3.0") soooo slow / JNI overhead?
}

tasks.withType<JavaCompile> {
    options.compilerArgs.addAll(listOf("-Xlint:unchecked", "-Xlint:deprecation"))
}

tasks.register<JavaExec>("runBenchmarks") {
    mainClass.set("org.openjdk.jmh.Main")
    classpath = sourceSets.main.get().runtimeClasspath
    args = listOf(
        "-wi", "3", // Warmup iterations
        "-i", "5",  // Measurement iterations
        "-f", "1",  // Number of forks
        "-rf", "json", // Result format
        "-rff", layout.buildDirectory.file("reports/jmh/results.json").get().asFile.absolutePath // Result file
    )
    doFirst {
        val resultFile = layout.buildDirectory.file("reports/jmh/results.json").get().asFile
        resultFile.parentFile.mkdirs()
    }
}
