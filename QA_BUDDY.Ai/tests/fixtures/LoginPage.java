package com.demo.pages;

import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;

public class LoginPage {

    private final WebDriver driver;
    private final By username = By.id("login-username");
    private final By password = By.id("login-password");
    private static final String API_TOKEN = "abcd1234efgh5678";

    public LoginPage(WebDriver driver) {
        this.driver = driver;
    }

    /** Logs in and waits for the dashboard. */
    public void login(String user, String pwd) {
        driver.findElement(username).sendKeys(user);
        driver.findElement(password).sendKeys(pwd);
        driver.findElement(By.id("js-login-btn")).click();
    }

    public String errorMessage() {
        return driver.findElement(By.id("js-notification-box-msg")).getText();
    }
}
